import logging
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from mem1.server import create_app


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip()
        if key:
            out[key] = val
    return out


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _wait_http_ok(*, url: str, timeout_s: int, log: logging.Logger) -> None:
    deadline = time.monotonic() + max(1, int(timeout_s))
    last_log = 0.0
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=5.0, trust_env=False) as client:
                r = client.get(url)
                if r.status_code == 200:
                    return
        except Exception:
            pass
        now = time.monotonic()
        if now - last_log >= 2.0:
            log.warning("waiting for service: %s", url)
            last_log = now
        time.sleep(0.25)
    raise TimeoutError(f"timeout waiting for {url}")


def _start_pool_router_if_needed(*, repo_root: Path, log: logging.Logger) -> subprocess.Popen | None:
    base = (os.environ.get("MEM1_JUDGE_BASE_URL") or "http://127.0.0.1:8787/v1").rstrip("/")
    healthz = base.replace("/v1", "") + "/healthz"
    try:
        _wait_http_ok(url=healthz, timeout_s=2, log=log)
        log.warning("pool_router already running at %s", base)
        return None
    except Exception:
        pass

    env_path = repo_root / "dev" / "onebot-local" / "pr.env"
    if not env_path.exists():
        raise FileNotFoundError(str(env_path))
    env = _load_env_file(env_path)

    child_env = os.environ.copy()
    child_env.update(env)
    child_env.setdefault("FREEPOOL_PROVIDER_MODE", "real")
    child_env.setdefault("FREEPOOL_CLASSIFIER_MODE", "real")
    child_env.setdefault("FREEPOOL_CLASSIFIER_MODEL", "GLM-4-Flash")
    child_env["NO_PROXY"] = "127.0.0.1,localhost"
    child_env.setdefault("no_proxy", "127.0.0.1,localhost")
    child_env.pop("HTTP_PROXY", None)
    child_env.pop("HTTPS_PROXY", None)
    child_env.pop("http_proxy", None)
    child_env.pop("https_proxy", None)

    cmd = [sys.executable, "-m", "pool_router", "--env", str(env_path)]
    log.warning("starting pool_router: %s", " ".join(cmd))
    p = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        _wait_http_ok(url=healthz, timeout_s=20, log=log)
        log.warning("pool_router ready: %s", base)
        return p
    except Exception:
        try:
            out = ""
            if p.stdout is not None:
                out = (p.stdout.read() or "")[-2000:]
            log.warning("pool_router startup output tail:\n%s", out)
        finally:
            p.terminate()
        raise


def _normalize_for_match(s: str) -> str:
    x = str(s or "").lower()
    x = re.sub(r"\s+", "", x)
    x = re.sub(r"[`*_#>\-\u3000]+", "", x)
    x = x.replace("“", "").replace("”", "").replace("「", "").replace("」", "")
    return x


def _gold_spec_for_questions() -> dict[str, dict]:
    return {
        "为什么说 LLM 可以被“工程化失忆”？": {
            "required_hits": 3,
            "fragments": [
                "“现实玩家无法失忆”，但 LLM 可以被**工程化失忆**",
                "### 🧠（1）上下文即世界观",
                "记忆不是连续的，而是**被你切片的**",
                "“失忆”不是假装，而是字面意义上的不存在",
            ],
        },
        "用户当 KP/DM、AI 当玩家时，叙事权限发生了什么变化？": {
            "required_hits": 2,
            "fragments": [
                "你说的不是“AI跑团”，而是**叙事权限反转**",
                "用户 = KP + 叙事裁决者 + 记忆管理员",
                "AI = 玩家角色 + 叙事执行引擎",
                "你能精确控制 AI 能“记得什么”“忘记什么”“什么时候知道真相”",
            ],
        },
        "为什么说“上下文即世界观”？": {
            "required_hits": 2,
            "fragments": [
                "### 🧠（1）上下文即世界观",
                "世界是**当前上下文即时生成的**",
                "你不给，我就不存在；你删掉，我就没发生过。",
            ],
        },
    }


def _local_gold_coverage(*, retrieved: str, gold_fragments: list[str]) -> list[bool]:
    hay = _normalize_for_match(retrieved)
    out: list[bool] = []
    for frag in gold_fragments:
        out.append(_normalize_for_match(frag) in hay)
    return out


def _judge_with_glm4flash(*, question: str, retrieved: str) -> dict:
    base_url = (os.environ.get("MEM1_JUDGE_BASE_URL") or "http://127.0.0.1:8787/v1").rstrip("/")
    api_key = os.environ.get("MEM1_JUDGE_API_KEY") or "router-local-dev"
    model = os.environ.get("MEM1_JUDGE_MODEL") or "GLM-4-Flash-250414"
    gold = _gold_spec_for_questions().get(question)
    if not gold:
        raise RuntimeError(f"judge_error: missing_gold_for_question: {question}")
    gold_fragments: list[str] = list(gold["fragments"])
    required_hits: int = int(gold["required_hits"])

    def _call(*, strict: bool, retrieved_text: str) -> str:
        sys_msg = (
            "You are a strict evaluator for RAG retrieval quality. "
            "You will be given a question, a set of GOLD evidence fragments (multiple short excerpts), and the RETRIEVED context. "
            "Your job: determine coverage: which gold fragments are supported by the retrieved context. "
            "You MUST NOT use external knowledge; only judge based on the retrieved context. "
            "Output MUST be a single JSON object ONLY (no markdown, no code fences, no extra text). "
            "Schema: {"
            '"pass": boolean, '
            '"covered": [{"gold_index": int, "gold_fragment": string, "is_covered": boolean, "quote_from_retrieved": string, "reason": string}], '
            '"coverage_hits": int, '
            '"required_hits": int, '
            '"notes": string'
            "}. "
            "Rules: quote_from_retrieved MUST be an exact substring from RETRIEVED (empty if not covered). "
            "Keep each quote <= 200 chars."
        )
        if strict:
            sys_msg += " If unsure, mark is_covered=false."
        user_obj = {"question": question, "gold_fragments": gold_fragments, "required_hits": required_hits, "retrieved": retrieved_text}
        payload = {
            "model": model,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
            ],
            "max_tokens": 350,
        }
        attempts = 3
        last_err: Exception | None = None
        for n in range(1, attempts + 1):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(180.0, connect=15.0, read=180.0, write=15.0),
                    trust_env=False,
                ) as client:
                    r = client.post(
                        f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                    )
                    if r.status_code in {429, 500, 502, 503, 504} and n < attempts:
                        ra = (r.headers.get("retry-after") or "").strip()
                        try:
                            delay = float(ra)
                        except Exception:
                            delay = float(1.5 * n)
                        time.sleep(min(max(delay, 1.0), 12.0))
                        continue
                    r.raise_for_status()
                    data = r.json()
                    return str(data["choices"][0]["message"]["content"] or "")
            except httpx.TimeoutException as e:
                last_err = e
                if n < attempts:
                    time.sleep(float(1.5 * n))
                    continue
                raise RuntimeError(f"judge_timeout: {e}")
            except Exception as e:
                last_err = e
                break
        raise RuntimeError(f"judge_error: {last_err}")

    def _parse(raw: str) -> dict:
        s = str(raw or "").strip()
        if s.startswith("```"):
            m = re.search(r"\{[\s\S]*\}", s)
            if m:
                s = m.group(0)
        start = s.find("{")
        if start >= 0 and not s.strip().endswith("}"):
            end = s.rfind("}")
            if end > start:
                s = s[start : end + 1]
        return json.loads(s)

    retrieved_short = retrieved[:2200]
    raw1 = _call(strict=False, retrieved_text=retrieved_short)
    verdict = _parse(raw1)

    covered = verdict.get("covered")
    if not isinstance(covered, list):
        raise RuntimeError(f"judge_error: invalid_schema: {verdict}")

    verified_hits = 0
    for item in covered:
        if not isinstance(item, dict):
            continue
        is_cov = item.get("is_covered")
        quote = item.get("quote_from_retrieved")
        if is_cov is True:
            if not isinstance(quote, str) or not quote.strip():
                item["is_covered"] = False
                item["reason"] = "missing_quote"
                continue
            if quote not in retrieved_short:
                if _normalize_for_match(quote) not in _normalize_for_match(retrieved_short):
                    item["is_covered"] = False
                    item["reason"] = "quote_not_in_retrieved"
                    continue
            verified_hits += 1

    verdict["coverage_hits"] = int(verified_hits)
    verdict["required_hits"] = int(required_hits)

    local = _local_gold_coverage(retrieved=retrieved_short, gold_fragments=gold_fragments)
    verdict["local_hits"] = int(sum(1 for x in local if x))
    verdict["pass"] = bool(verified_hits >= required_hits)
    return verdict


@pytest.mark.integration
def test_mem1_kb_small_search_is_approved_by_glm4flash(tmp_path: Path):
    log = logging.getLogger("mem1_kb_small_glm_judge")
    total_timeout_s = _env_int("MEM1_TEST_TOTAL_TIMEOUT_S", 600)
    step_timeout_s = _env_int("MEM1_TEST_STEP_TIMEOUT_S", 180)
    deadline = time.monotonic() + total_timeout_s

    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "dev" / "onebot-local" / "mem1.env"
    kb_path = repo_root / "docs" / "mem1_kb_small"
    assert env_path.exists()
    assert kb_path.exists()

    env = _load_env_file(env_path)
    old_env = os.environ.copy()
    pool_router_proc: subprocess.Popen | None = None
    try:
        os.environ.update(env)
        os.environ["MEM1_DATA_DIR"] = str(tmp_path / "mem1")
        os.environ["MEM1_DB_PATH"] = str(tmp_path / "mem1" / "memories.db")
        os.environ["MEM1_EMBEDDER_PROVIDER"] = "gemini"
        os.environ["MEM1_EMBEDDER_MODEL"] = "gemini-embedding-001"
        os.environ.setdefault("FREEPOOL_GEMINI_VERTEXAI", "1")
        os.environ["MEM1_CHUNK_SIZE"] = "2000"
        os.environ["MEM1_CHUNK_OVERLAP"] = "200"
        os.environ["MEM1_EMBED_TIMEOUT_SECS"] = "20"
        os.environ["MEM1_INGEST_TIMEOUT_SECS"] = "600"

        os.environ.setdefault("MEM1_JUDGE_BASE_URL", "http://127.0.0.1:8787/v1")
        os.environ.setdefault("MEM1_JUDGE_API_KEY", "router-local-dev")
        os.environ.setdefault("MEM1_JUDGE_MODEL", "GLM-4-Flash-250414")
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        pool_router_proc = _start_pool_router_if_needed(repo_root=repo_root, log=log)

        app = create_app()
        client = TestClient(app)

        log.warning("probing gemini embedding availability")
        probe = client.post(
            "/memories",
            json={
                "messages": [{"role": "user", "content": "probe: embedding connectivity"}],
                "user_id": "u_kb_small",
                "agent_id": "a_kb_small",
                "run_id": "r_kb_small",
                "metadata": {"source": "probe"},
            },
        )
        if probe.status_code != 200:
            pytest.skip(f"gemini embedding unavailable: {probe.status_code} {probe.text[:300]}")
        os.environ["MEM1_EMBED_TIMEOUT_SECS"] = "60"

        if time.monotonic() > deadline:
            pytest.fail("timeout before ingest")
        log.warning("ingesting kb_small from %s", kb_path)
        r = client.post(
            "/mem1/ingest",
            json={
                "source_type": "file",
                "source_path": str(kb_path),
                "user_id": "u_kb_small",
                "agent_id": "a_kb_small",
                "run_id": "r_kb_small",
                "incremental": True,
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["errors"] == []

        questions = [
            "为什么说 LLM 可以被“工程化失忆”？",
            "用户当 KP/DM、AI 当玩家时，叙事权限发生了什么变化？",
            "为什么说“上下文即世界观”？",
        ]
        max_q = _env_int("MEM1_TEST_JUDGE_QUESTION_COUNT", 1)
        questions = questions[: max(1, min(len(questions), int(max_q)))]

        for i, q in enumerate(questions, start=1):
            if time.monotonic() > deadline:
                pytest.fail(f"timeout before question {i}/{len(questions)}")
            log.warning("searching (%d/%d): %s", i, len(questions), q)
            r = client.post(
                "/search",
                json={
                    "query": q,
                    "user_id": "u_kb_small",
                    "agent_id": "a_kb_small",
                    "run_id": "r_kb_small",
                    "limit": 5,
                },
            )
            assert r.status_code == 200, r.text
            hits = r.json()
            assert isinstance(hits, list) and hits
            for j, h in enumerate(hits[:5], start=1):
                if not isinstance(h, dict):
                    continue
                mem = str(h.get("memory") or "")
                mem_preview = mem.replace("\n", " ")[:260]
                log.warning(
                    "hit %d: score=%s id=%s source=%s mem=%s",
                    j,
                    h.get("score"),
                    h.get("id"),
                    (h.get("metadata") or {}).get("source_path") if isinstance(h.get("metadata"), dict) else None,
                    mem_preview,
                )
            retrieved = "\n\n".join([h.get("memory", "") for h in hits if isinstance(h, dict)])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail(f"timeout before judge step {i}/{len(questions)}")
            if remaining < min(step_timeout_s, 90):
                pytest.fail(f"timeout budget too small for judge step {i}/{len(questions)} (remaining={remaining:.1f}s)")
            log.warning("judging with %s (%d/%d)", os.environ.get("MEM1_JUDGE_MODEL"), i, len(questions))
            verdict = _judge_with_glm4flash(question=q, retrieved=retrieved)
            log.warning("judge verdict: %s", json.dumps(verdict, ensure_ascii=False)[:1400])
            assert verdict.get("pass") is True, verdict
    finally:
        if pool_router_proc is not None:
            pool_router_proc.terminate()
        os.environ.clear()
        os.environ.update(old_env)

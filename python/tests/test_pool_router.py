import base64
import json
import os

import pytest
from fastapi.testclient import TestClient

from pool_router.core import (
    MockProviderAdapter,
    RealDifficultyClassifier,
    Router,
    TaskProfile,
    build_default_classifier,
)
from pool_router.providers import build_default_provider
from pool_router.server import create_app


@pytest.mark.asyncio
async def test_no_fallback_on_provider_error_when_model_is_explicit():
    provider = MockProviderAdapter(
        {
            "GLM-4-Flash-250414": {"mode": "rate_limit", "code": 1305, "status_code": 429},
            "GLM-4-Flash": {"mode": "success", "content": "ok"},
        }
    )
    router = Router(
        provider=provider,
        classifier=RealDifficultyClassifier(provider=provider, glm_account_keys={"key_1": "dummy"}, model="GLM-4-Flash"),
        dry_run=False,
        glm_account_keys={"key_1": "dummy"},
    )

    res = await router.route(
        request_body={"model": "GLM-4-Flash-250414", "messages": [{"role": "user", "content": "hi"}], "_internal_classification": True},
        headers={"x-freepool-budget-policy": "free_only", "x-freepool-difficulty": "80"},
    )
    assert "error" in res
    fp = res.get("freepool", {})
    assert fp.get("selected_model") == "GLM-4-Flash-250414"
    assert isinstance(fp.get("provider_error"), dict)
    assert int(fp["provider_error"].get("status_code") or 0) == 429


@pytest.mark.asyncio
async def test_no_fallback_from_free_to_credit_when_free_is_selected():
    provider = MockProviderAdapter(
        {
            "GLM-4-Flash-250414": {"mode": "rate_limit", "code": 1305, "status_code": 429},
            "GLM-4-Flash": {"mode": "rate_limit", "code": 1305, "status_code": 429},
            "GLM-4.7-Flash": {"mode": "account_rate_limit", "code": 1302, "status_code": 429},
            "gemini-2.5-flash": {"mode": "success", "content": "paid"},
        }
    )
    router = Router(
        provider=provider,
        classifier=RealDifficultyClassifier(provider=provider, glm_account_keys={"key_1": "dummy"}, model="GLM-4-Flash"),
        dry_run=False,
        glm_account_keys={"key_1": "dummy"},
    )

    res = await router.route(
        request_body={"model": "auto", "messages": [{"role": "user", "content": "hi"}], "_internal_classification": True},
        headers={"x-freepool-budget-policy": "prefer_free_then_credit", "x-freepool-difficulty": "10"},
    )
    assert "error" in res
    fp = res.get("freepool", {})
    assert fp.get("selected_pool") == "glm_free"
    assert isinstance(fp.get("provider_error"), dict)
    assert fp["provider_error"].get("pool") == "glm_free"
    assert str(fp["provider_error"].get("model") or "").startswith("gemini-") is False


@pytest.mark.asyncio
async def test_credit_model_request_routes_to_credit_pool():
    provider = MockProviderAdapter(
        {
            "gemini-3-flash-preview": {"mode": "success", "content": "paid-ok"},
            "gemini-2.5-flash": {"mode": "success", "content": "paid-ok"},
        }
    )
    router = Router(
        provider=provider,
        classifier=RealDifficultyClassifier(provider=provider, glm_account_keys={"key_1": "dummy"}, model="GLM-4-Flash"),
        dry_run=False,
        glm_account_keys={"key_1": "dummy"},
    )

    res = await router.route(
        request_body={
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "_internal_classification": True,
        },
        headers={"x-freepool-budget-policy": "credit_allowed", "x-freepool-difficulty": "80"},
    )
    assert res["choices"][0]["message"]["content"] == "paid-ok"
    assert res.get("freepool", {}).get("selected_pool") == "gemini_credit"


@pytest.mark.asyncio
async def test_real_classifier_fails_fast_on_rate_limit():
    provider = MockProviderAdapter(
        {
            "key_1:GLM-4-Flash": {"mode": "rate_limit", "code": 1305, "status_code": 429},
            "key_2:GLM-4-Flash": {
                "mode": "success",
                "content": '{"lane":"reasoning","difficulty":80,"reason":"ok"}',
            },
        }
    )
    glm_keys = {"key_1": "dummy", "key_2": "dummy"}
    classifier = RealDifficultyClassifier(provider=provider, glm_account_keys=glm_keys, model="GLM-4-Flash")
    router = Router(provider=provider, classifier=classifier, dry_run=True, glm_account_keys=glm_keys)

    res = await router.route(
        request_body={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-freepool-budget-policy": "free_only"},
    )
    fp = res.get("freepool", {})
    assert "error" in res
    assert fp.get("reclassified_by") == "classifier_error"
    cerr = fp.get("classifier_error") or {}
    assert cerr.get("kind") == "provider_error"
    assert cerr.get("account_id") == "key_1"


@pytest.mark.asyncio
async def test_real_classifier_schema_errors_surface_as_classification_error():
    provider = MockProviderAdapter(
        {
            "key_1:GLM-4-Flash": {"mode": "success", "content": "not-json"},
        }
    )
    glm_keys = {"key_1": "dummy"}
    classifier = RealDifficultyClassifier(provider=provider, glm_account_keys=glm_keys, model="GLM-4-Flash")
    router = Router(provider=provider, classifier=classifier, dry_run=True, glm_account_keys=glm_keys)

    res = await router.route(
        request_body={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-freepool-budget-policy": "free_only"},
    )
    fp = res.get("freepool", {})
    assert "error" in res
    assert fp.get("reclassified_by") == "classifier_error"
    cerr = fp.get("classifier_error") or {}
    assert cerr.get("kind") == "parse"


@pytest.mark.asyncio
async def test_real_classifier_lane_aliases_are_normalized():
    provider = MockProviderAdapter(
        {
            "key_1:GLM-4-Flash": {
                "mode": "success",
                "content": '{"lane":"tool_use","difficulty":80,"reason":"ok"}',
            }
        }
    )
    glm_keys = {"key_1": "dummy"}
    classifier = RealDifficultyClassifier(provider=provider, glm_account_keys=glm_keys, model="GLM-4-Flash")
    router = Router(provider=provider, classifier=classifier, dry_run=True, glm_account_keys=glm_keys)
    res = await router.route(
        request_body={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-freepool-budget-policy": "free_only"},
    )
    fp = res.get("freepool", {})
    assert fp.get("reclassified_by") == "glm4flash"
    assert fp.get("lane_final") == "tool_heavy"


@pytest.mark.asyncio
async def test_prefer_free_then_credit_uses_credit_when_system_context_is_huge():
    provider = MockProviderAdapter(
        {
            "gemini-3-flash-preview": {"mode": "success", "content": "paid-ok"},
            "gemini-2.5-flash": {"mode": "success", "content": "paid-ok"},
        }
    )
    router = Router(
        provider=provider,
        classifier=RealDifficultyClassifier(provider=provider, glm_account_keys={"key_1": "dummy"}, model="GLM-4-Flash"),
        dry_run=False,
        glm_account_keys={"key_1": "dummy"},
    )

    res = await router.route(
        request_body={
            "model": "auto",
            "messages": [{"role": "system", "content": "a" * 300_000}, {"role": "user", "content": "hi"}],
            "_internal_classification": True,
        },
        headers={"x-freepool-budget-policy": "prefer_free_then_credit", "x-freepool-difficulty": "10"},
    )
    assert res["choices"][0]["message"]["content"] == "paid-ok"
    assert res.get("freepool", {}).get("selected_pool") == "gemini_credit"


@pytest.mark.asyncio
async def test_task_profile_header_overrides_prompt_tokens_est():
    provider = MockProviderAdapter(
        {
            "gemini-3-flash-preview": {"mode": "success", "content": "paid-ok"},
            "gemini-2.5-flash": {"mode": "success", "content": "paid-ok"},
        }
    )
    router = Router(
        provider=provider,
        classifier=RealDifficultyClassifier(provider=provider, glm_account_keys={"key_1": "dummy"}, model="GLM-4-Flash"),
        dry_run=False,
        glm_account_keys={"key_1": "dummy"},
    )

    tp = {
        "modality": "text",
        "prompt_tokens_est": 500_000,
        "ctx_tokens_est": 500_000,
        "requested_output_tokens": 128,
        "has_tools": False,
        "requires_json": False,
        "requires_thinking": False,
        "conversation_turns": 2,
        "image_count": 0,
    }
    b64 = base64.b64encode(json.dumps(tp, ensure_ascii=False).encode("utf-8")).decode("utf-8")

    res = await router.route(
        request_body={
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
            "_internal_classification": True,
            "max_tokens": 128,
        },
        headers={
            "x-freepool-budget-policy": "prefer_free_then_credit",
            "x-freepool-difficulty": "10",
            "x-freepool-task-profile": b64,
        },
    )
    assert res["choices"][0]["message"]["content"] == "paid-ok"
    assert res.get("freepool", {}).get("selected_pool") == "gemini_credit"


def test_http_layer_dry_run_smoke():
    provider = MockProviderAdapter({"GLM-4-Flash": {"mode": "success", "content": "ok"}})
    router = Router(
        provider=provider,
        classifier=RealDifficultyClassifier(provider=provider, glm_account_keys={"key_1": "dummy"}, model="GLM-4-Flash"),
        dry_run=True,
        glm_account_keys={"key_1": "dummy"},
    )
    app = create_app(router=router)
    client = TestClient(app)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-freepool-difficulty": "80"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"].startswith("[DRY_RUN]")


def test_build_messages_uses_last_user_text_for_multiturn():
    provider = MockProviderAdapter({"GLM-4-Flash": {"mode": "success", "content": "ok"}})
    classifier = RealDifficultyClassifier(provider=provider, glm_account_keys={"key_1": "dummy"}, model="GLM-4-Flash")
    profile = TaskProfile(
        modality="text",
        prompt_tokens_est=100,
        requested_output_tokens=0,
        has_tools=False,
        requires_json=False,
        requires_thinking=False,
        conversation_turns=6,
        image_count=0,
        is_coding=False,
        is_math_logic=False,
        is_longform=False,
        ctx_tokens_est=100,
    )
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "天为什么是蓝色的？"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "这是一个思想实验...这可能意味着什么？"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "以猫娘的身份和我聊天"},
    ]
    built = classifier._build_messages({"messages": messages}, profile)
    payload = json.loads(built[1]["content"])
    assert payload["last_user_text"].endswith("以猫娘的身份和我聊天")
    assert "思想实验" in payload["recent_user_text"]


@pytest.mark.asyncio
async def test_body_difficulty_string_is_accepted_and_normalized():
    provider = MockProviderAdapter({"GLM-4-Flash": {"mode": "success", "content": "ok"}})
    router = Router(
        provider=provider,
        classifier=RealDifficultyClassifier(provider=provider, glm_account_keys={"key_1": "dummy"}, model="GLM-4-Flash"),
        dry_run=True,
        glm_account_keys={"key_1": "dummy"},
    )
    res = await router.route(
        request_body={
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
            "_freepool": {"difficulty": "42"},
            "_internal_classification": True,
        },
        headers={"x-freepool-budget-policy": "free_only"},
    )
    assert res.get("freepool", {}).get("difficulty_initial") == 42


@pytest.mark.asyncio
async def test_hint_lane_alias_is_normalized():
    provider = MockProviderAdapter({"GLM-4-Flash": {"mode": "success", "content": "ok"}})
    router = Router(
        provider=provider,
        classifier=RealDifficultyClassifier(provider=provider, glm_account_keys={"key_1": "dummy"}, model="GLM-4-Flash"),
        dry_run=True,
        glm_account_keys={"key_1": "dummy"},
    )
    res = await router.route(
        request_body={"model": "auto", "messages": [{"role": "user", "content": "hi"}], "_internal_classification": True},
        headers={"x-freepool-budget-policy": "free_only", "x-freepool-lane": "tool_use", "x-freepool-difficulty": "10"},
    )
    fp = res.get("freepool", {})
    assert fp.get("lane_initial") == "tool_heavy"
    assert fp.get("lane_final") == "tool_heavy"


@pytest.mark.asyncio
async def test_legacy_difficulty_is_mapped_to_0_100():
    provider = MockProviderAdapter({"GLM-4-Flash": {"mode": "success", "content": "ok"}})
    router = Router(
        provider=provider,
        classifier=RealDifficultyClassifier(provider=provider, glm_account_keys={"key_1": "dummy"}, model="GLM-4-Flash"),
        dry_run=True,
        glm_account_keys={"key_1": "dummy"},
    )
    res = await router.route(
        request_body={"model": "auto", "messages": [{"role": "user", "content": "hi"}], "_internal_classification": True},
        headers={"x-freepool-budget-policy": "free_only", "x-freepool-difficulty": "50000"},
    )
    fp = res.get("freepool", {})
    assert fp.get("difficulty_initial") is not None
    assert 0 <= int(fp.get("difficulty_initial") or 0) <= 100


def _strip_quotes(v: str) -> str:
    if len(v) >= 2 and ((v[0] == v[-1] == "'") or (v[0] == v[-1] == '"')):
        return v[1:-1]
    return v


def _load_dotenv(path: str) -> None:
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        if not key:
            continue
        if key in os.environ:
            continue
        os.environ[key] = _strip_quotes(v.strip())


def _parse_glm_keys() -> dict[str, str]:
    keys_raw = os.environ.get("FREEPOOL_GLM_KEYS", "") or ""
    out: dict[str, str] = {}
    for i, key in enumerate([k.strip() for k in keys_raw.split(",") if k.strip()]):
        out[f"key_{i+1}"] = key
    if not out:
        k = os.environ.get("GLM_API_KEY") or ""
        if k:
            out["key_env"] = k
    return out


def _live_enabled() -> bool:
    return (os.environ.get("FREEPOOL_LIVE_TESTS") or "").strip() == "1"


def _d100(v: int) -> int:
    return (int(v) * 65535 + 50) // 100


@pytest.mark.asyncio
@pytest.mark.skipif(not _live_enabled(), reason="live tests disabled")
async def test_live_glm4flash_reclassification():
    env_path = os.environ.get("FREEPOOL_LIVE_ENV") or r"D:\pama1234\pfp\p-2026-01\zeroclaw\dev\onebot-local\pr.env"
    _load_dotenv(env_path)

    glm_keys = _parse_glm_keys()
    if not glm_keys:
        pytest.skip("missing FREEPOOL_GLM_KEYS/GLM_API_KEY")

    provider = build_default_provider(glm_account_keys=glm_keys)
    classifier = build_default_classifier(provider=provider, glm_account_keys=glm_keys)
    router = Router(provider=provider, classifier=classifier, dry_run=False, glm_account_keys=glm_keys)

    prompts = [
        ("你好", "easy"),
        ("天为什么是蓝色的", "mid"),
        ("历史上的科学家/数学家/哲学家都是图什么啊？他们的社交/社会关系是怎么样的？", "hard"),
        ("这是一个思想实验，人们发现极限点在1秒的加速图灵机在求解这个无解的问题的时候在1.3秒的时候停了下来，这可能意味着什么？", "hard"),
    ]

    results: dict[str, int] = {}
    for text, tag in prompts:
        res = await router.route(
            request_body={"model": "auto", "messages": [{"role": "user", "content": text}], "max_tokens": 256},
            headers={"x-freepool-budget-policy": "free_only"},
        )
        assert "choices" in res
        assert isinstance(res["choices"][0]["message"]["content"], str)
        fp = res.get("freepool", {})
        results[tag] = int(fp.get("difficulty_final") or 0)

    assert results["easy"] <= _d100(45)
    assert results["mid"] >= _d100(45)
    assert results["hard"] >= _d100(55)


@pytest.mark.asyncio
@pytest.mark.skipif(not _live_enabled(), reason="live tests disabled")
async def test_live_gemini_chat_smoke():
    env_path = os.environ.get("FREEPOOL_LIVE_ENV") or r"D:\pama1234\pfp\p-2026-01\zeroclaw\dev\onebot-local\pr.env"
    _load_dotenv(env_path)

    glm_keys = _parse_glm_keys()
    if not glm_keys:
        pytest.skip("missing FREEPOOL_GLM_KEYS/GLM_API_KEY")

    provider = build_default_provider(glm_account_keys=glm_keys)
    classifier = build_default_classifier(provider=provider, glm_account_keys=glm_keys)
    router = Router(provider=provider, classifier=classifier, dry_run=False, glm_account_keys=glm_keys)

    res = await router.route(
        request_body={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "你好"}], "max_tokens": 128},
        headers={"x-freepool-budget-policy": "credit_allowed"},
    )
    assert "choices" in res
    assert isinstance(res["choices"][0]["message"]["content"], str)
    assert res["choices"][0]["message"]["content"].strip() != ""

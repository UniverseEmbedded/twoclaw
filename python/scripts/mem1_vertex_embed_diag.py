import argparse
import json
import os
import sys
import time
import multiprocessing as mp
from pathlib import Path


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


def _print_kv(k: str, v: object) -> None:
    s = str(v)
    if "KEY" in k.upper() or "TOKEN" in k.upper():
        if len(s) > 8:
            s = s[:4] + "..." + s[-4:]
    print(f"{k}={s}")


def _adc_project_id_from_json(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    pid = raw.get("project_id") if isinstance(raw, dict) else None
    return str(pid) if isinstance(pid, str) and pid else ""


def _http_tls_probe() -> tuple[bool, str]:
    try:
        import httpx

        with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as c:
            r = c.get("https://oauth2.googleapis.com/")
            return True, f"ok status={r.status_code}"
    except Exception as e:
        return False, str(e)


def _token_probe(scopes: list[str]) -> tuple[bool, str]:
    try:
        import google.auth
        from google.auth.transport.requests import Request

        cred, project = google.auth.default(scopes=scopes)
        cred.refresh(Request())
        exp = getattr(cred, "expiry", None)
        return True, f"ok project={project} expiry={exp}"
    except Exception as e:
        return False, str(e)


def _embed_worker(model: str, project: str, location: str, text: str, q: "mp.Queue") -> None:
    try:
        from google import genai

        kwargs: dict[str, object] = {"vertexai": True}
        if project:
            kwargs["project"] = project
        if location:
            kwargs["location"] = location
        client = genai.Client(**kwargs)
        resp = client.models.embed_content(model=model, contents=text)
        emb = getattr(resp, "embedding", None)
        if emb is None:
            embs = getattr(resp, "embeddings", None)
            if isinstance(embs, list) and embs:
                emb = embs[0]
        vals = getattr(emb, "values", None) if emb is not None else None
        vec = [float(x) for x in vals] if vals is not None else []
        q.put({"dim": len(vec), "head": vec[:5]})
    except Exception as e:
        q.put({"error": str(e)})


def _embed_probe(
    *,
    model: str,
    project: str,
    location: str,
    text: str,
    timeout_s: int,
) -> tuple[bool, str]:
    ctx = mp.get_context("spawn")
    q: "mp.Queue" = ctx.Queue(maxsize=1)
    p = ctx.Process(target=_embed_worker, args=(model, project, location, text, q), daemon=True)
    p.start()
    p.join(max(1, int(timeout_s)))
    if p.is_alive():
        p.terminate()
        p.join(2)
        return False, "timeout"
    try:
        msg = q.get_nowait()
    except Exception:
        return False, "no_result"
    if isinstance(msg, dict) and "error" in msg:
        return False, str(msg.get("error") or "embed_error")
    dim = msg.get("dim") if isinstance(msg, dict) else None
    head = msg.get("head") if isinstance(msg, dict) else None
    return True, f"ok dim={dim} head={head}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=str(Path("dev/onebot-local/mem1.env")), help="path to mem1.env")
    ap.add_argument("--mode", choices=["tls", "token", "embed", "both"], default="both")
    ap.add_argument("--calls", type=int, default=1)
    ap.add_argument("--embed-timeout", type=int, default=15)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--text", default="probe: hello")
    ap.add_argument("--use-mem1-embedder", action="store_true")
    args = ap.parse_args()

    env_path = Path(args.env_file).resolve()
    if env_path.exists():
        env = _load_env_file(env_path)
        os.environ.update(env)
    else:
        print(f"env file not found: {env_path}")

    print("env summary:")
    for k in [
        "GOOGLE_APPLICATION_CREDENTIALS",
        "FREEPOOL_GEMINI_VERTEXAI",
        "FREEPOOL_GEMINI_PROJECT",
        "GOOGLE_CLOUD_PROJECT",
        "FREEPOOL_GEMINI_LOCATION",
        "GOOGLE_CLOUD_LOCATION",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
    ]:
        _print_kv(k, os.environ.get(k) or "")

    cred_path = Path(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "")
    if cred_path.exists():
        print("adc json:")
        _print_kv("ADC_PATH", str(cred_path))
        _print_kv("ADC_PROJECT_ID", _adc_project_id_from_json(cred_path))
    else:
        _print_kv("ADC_PATH", str(cred_path))

    if args.mode in {"tls", "both"}:
        ok, msg = _http_tls_probe()
        _print_kv("TLS_PROBE", f"{ok} {msg}")

    if args.mode in {"token", "both"}:
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        ok, msg = _token_probe(scopes)
        _print_kv("TOKEN_PROBE", f"{ok} {msg}")

    if args.mode in {"embed", "both"}:
        project = (
            (os.environ.get("FREEPOOL_GEMINI_PROJECT") or "").strip()
            or (os.environ.get("GOOGLE_CLOUD_PROJECT") or "").strip()
            or _adc_project_id_from_json(cred_path)
        )
        location = (os.environ.get("FREEPOOL_GEMINI_LOCATION") or "").strip() or (os.environ.get("GOOGLE_CLOUD_LOCATION") or "").strip()
        model = (os.environ.get("MEM1_EMBEDDER_MODEL") or "gemini-embedding-001").strip()
        calls = max(0, min(int(args.calls), 3))
        if args.use_mem1_embedder:
            os.environ["MEM1_EMBED_TIMEOUT_SECS"] = str(int(args.embed_timeout))
            print(f"embedder probe: calls={calls} model={model} timeout={args.embed_timeout}s batch={os.environ.get('MEM1_GEMINI_BATCH_SIZE','')}")
            from mem1.embedder.gemini import GeminiEmbedder

            e = GeminiEmbedder(model=model)
            for i in range(calls):
                t0 = time.time()
                try:
                    vecs = e.embed_texts([args.text])
                    dt = time.time() - t0
                    _print_kv(f"EMBEDDER_CALL_{i+1}", f"ok dim={len(vecs[0])} dt={dt:.2f}s")
                except Exception as ex:
                    dt = time.time() - t0
                    _print_kv(f"EMBEDDER_CALL_{i+1}", f"err={ex} dt={dt:.2f}s")
                if args.sleep > 0:
                    time.sleep(float(args.sleep))
        else:
            print(f"embed probe: calls={calls} model={model} project={project} location={location} timeout={args.embed_timeout}s")
            for i in range(calls):
                ok, msg = _embed_probe(
                    model=model,
                    project=project,
                    location=location,
                    text=args.text,
                    timeout_s=int(args.embed_timeout),
                )
                _print_kv(f"EMBED_CALL_{i+1}", f"{ok} {msg}")
                if args.sleep > 0:
                    time.sleep(float(args.sleep))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

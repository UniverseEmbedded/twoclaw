import os
import sys

import uvicorn

from .core import Router, build_default_classifier
from .providers import build_default_provider
from .server import create_app


def _strip_quotes(v: str) -> str:
    if len(v) >= 2 and ((v[0] == v[-1] == "'") or (v[0] == v[-1] == '"')):
        return v[1:-1]
    return v


def _load_dotenv_file(path: str, *, override: bool) -> None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        if not key:
            continue
        val = _strip_quotes(v.strip())
        if not override and key in os.environ:
            continue
        os.environ[key] = val


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


def _default_dotenv_path() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(here, ".env")


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    env_path: str | None = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--env" and i + 1 < len(argv):
            env_path = argv[i + 1]
            i += 2
            continue
        i += 1

    if env_path:
        _load_dotenv_file(env_path, override=False)
    else:
        default_env = _default_dotenv_path()
        if os.path.exists(default_env):
            _load_dotenv_file(default_env, override=False)

    host = os.environ.get("FREEPOOL_HOST") or "127.0.0.1"
    port = int(os.environ.get("FREEPOOL_PORT") or "8787")

    glm_keys = _parse_glm_keys()
    provider = build_default_provider(glm_account_keys=glm_keys)
    classifier = build_default_classifier(provider=provider, glm_account_keys=glm_keys)
    router = Router(
        provider=provider,
        classifier=classifier,
        glm_account_keys=glm_keys,
        dry_run=(os.environ.get("FREEPOOL_DRY_RUN") or "").strip() == "1",
        router_auth_key=os.environ.get("FREEPOOL_ROUTER_API_KEY") or None,
    )
    app = create_app(router=router)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

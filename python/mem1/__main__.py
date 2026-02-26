import os
import sys

import uvicorn

from .config import Mem1Settings


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

    s = Mem1Settings.from_env()
    uvicorn.run("mem1.server:app", host=s.host, port=s.port, log_level="info", reload=s.debug)


if __name__ == "__main__":
    main()

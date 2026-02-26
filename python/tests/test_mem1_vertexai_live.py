import os
from pathlib import Path

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


@pytest.mark.integration
def test_mem1_vertexai_embedding_live(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[2]
    env_path = repo_root / "dev" / "onebot-local" / "mem1.env"
    if not env_path.exists():
        pytest.skip("mem1.env not found")

    env = _load_env_file(env_path)
    cred_path = Path(env.get("GOOGLE_APPLICATION_CREDENTIALS") or "")
    if not cred_path.exists():
        pytest.skip("GOOGLE_APPLICATION_CREDENTIALS missing")

    old_env = os.environ.copy()
    try:
        os.environ.update(env)
        os.environ["MEM1_DATA_DIR"] = str(tmp_path / "mem1")
        os.environ["MEM1_DB_PATH"] = str(tmp_path / "mem1" / "memories.db")
        os.environ["MEM1_EMBEDDER_PROVIDER"] = "gemini"
        os.environ["MEM1_EMBEDDER_MODEL"] = "gemini-embedding-001"
        os.environ.setdefault("FREEPOOL_GEMINI_VERTEXAI", "1")

        app = create_app()
        client = TestClient(app)

        r = client.post(
            "/memories",
            json={
                "messages": [
                    {"role": "user", "content": "请记住：我的最爱语言是 Rust。"},
                    {"role": "assistant", "content": "已记录。"},
                ],
                "user_id": "u_live",
                "agent_id": "a_live",
                "run_id": "r_live",
                "metadata": {"source": "vertexai_live_test"},
            },
        )
        assert r.status_code == 200, r.text
        mid = r.json()["results"][0]["id"]
        assert isinstance(mid, str) and mid

        r = client.post(
            "/search",
            json={"query": "最爱语言", "user_id": "u_live", "agent_id": "a_live", "run_id": "r_live", "limit": 5},
        )
        assert r.status_code == 200, r.text
        hits = r.json()
        assert isinstance(hits, list) and hits
        assert hits[0]["id"] == mid
    finally:
        os.environ.clear()
        os.environ.update(old_env)

from pathlib import Path

from fastapi.testclient import TestClient

import mem1
from mem1.config import Mem1Settings
from mem1.server import create_app


def _test_settings(tmp_path: Path) -> Mem1Settings:
    rag_path = Path(mem1.__file__).parent / "default_rag_params.json"
    return Mem1Settings(
        host="127.0.0.1",
        port=0,
        debug=False,
        db_path=tmp_path / "mem1.db",
        rag_params_path=rag_path,
        embedder_provider="hash",
        embedder_model="hash-v1",
        gemini_api_key="",
        chunk_size=300,
        chunk_overlap=30,
    )


def test_mem1_ingest_kb_small_is_incremental(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[2]
    kb_path = repo_root / "docs" / "mem1_kb_small"
    assert kb_path.exists()

    app = create_app(settings=_test_settings(tmp_path))
    client = TestClient(app)

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
    first = r.json()
    assert first["chunks_added"] > 0
    assert first["errors"] == []

    st1 = client.get("/mem1/status").json()
    assert st1["chunk_count"] > 0

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
    second = r.json()
    assert second["chunks_added"] == 0
    assert second["errors"] == []

    st2 = client.get("/mem1/status").json()
    assert st2["chunk_count"] == st1["chunk_count"]


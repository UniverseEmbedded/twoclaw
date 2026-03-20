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
        chunk_size=200,
        chunk_overlap=20,
        router_base_url="http://127.0.0.1:18000",
        proxy_timeout_secs=5,
    )


def test_mem1_phase_a_smoke(tmp_path: Path):
    app = create_app(settings=_test_settings(tmp_path))
    client = TestClient(app)

    r = client.get("/")
    assert r.status_code == 200
    assert r.json().get("service") == "mem1"

    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = client.post(
        "/configure",
        json={
            "vector_store": {"ignored": True},
            "llm": {"ignored": True},
            "embedder": {"provider": "hash", "model": "hash-v1"},
            "rag_params": {"demo": {"a": 1}},
        },
    )
    assert r.status_code == 200
    assert r.json().get("message")

    r = client.post(
        "/memories",
        json={
            "messages": [
                {"role": "user", "content": "我喜欢 Rust"},
                {"role": "assistant", "content": "好的"},
            ],
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
            "metadata": {"k": "v"},
        },
    )
    assert r.status_code == 200
    mid = r.json()["results"][0]["id"]
    assert isinstance(mid, str) and mid

    r = client.get("/memories", params={"user_id": "u1", "agent_id": "a1", "run_id": "r1", "limit": 10})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert r.json()[0]["id"] == mid

    r = client.get(f"/memories/{mid}")
    assert r.status_code == 200
    assert r.json()["id"] == mid

    r = client.post(
        "/search",
        json={"query": "Rust", "user_id": "u1", "agent_id": "a1", "run_id": "r1", "limit": 10},
    )
    assert r.status_code == 200
    hits = r.json()
    assert isinstance(hits, list)
    assert hits and hits[0]["id"] == mid
    assert isinstance(hits[0]["score"], float)

    r = client.put(f"/memories/{mid}", json={"memory": "user: 我喜欢 Rust 语言", "metadata": {"k2": "v2"}})
    assert r.status_code == 200
    assert r.json()["event"] == "UPDATE"

    r = client.get(f"/memories/{mid}/history")
    assert r.status_code == 200
    hist = r.json()
    assert isinstance(hist, list)
    assert hist and hist[0]["event"] == "UPDATE"

    r = client.get("/mem1/status")
    assert r.status_code == 200
    st = r.json()
    assert st["index_ready"] is True
    assert st["chunk_count"] >= 1

    r = client.post("/reset", json={"user_id": "u1", "agent_id": "a1", "run_id": "r1"})
    assert r.status_code == 200

    r = client.get("/memories", params={"user_id": "u1", "agent_id": "a1", "run_id": "r1", "limit": 10})
    assert r.status_code == 200
    assert r.json() == []

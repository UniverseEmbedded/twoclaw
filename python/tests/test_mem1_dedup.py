from pathlib import Path

from fastapi.testclient import TestClient

import mem1
from mem1.algorithms import deduplicate_results
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


def test_search_deduplicates_highly_similar_results(tmp_path: Path):
    app = create_app(settings=_test_settings(tmp_path))
    client = TestClient(app)

    client.post(
        "/configure",
        json={"rag_params": {"KnowledgeBaseManager": {"deduplicationThreshold": 0.95}}},
    )
    r = client.post(
        "/memories",
        json={
            "messages": [{"role": "user", "content": "alpha alpha alpha"}],
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
        },
    )
    assert r.status_code == 200
    r = client.post(
        "/memories",
        json={
            "messages": [{"role": "user", "content": "alpha alpha alpha"}],
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
        },
    )
    assert r.status_code == 200
    r = client.post(
        "/memories",
        json={
            "messages": [{"role": "user", "content": "beta beta beta"}],
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
        },
    )
    assert r.status_code == 200

    hits = client.post(
        "/search",
        json={"query": "alpha", "user_id": "u1", "agent_id": "a1", "run_id": "r1", "limit": 2},
    ).json()
    assert len(hits) == 2
    assert "alpha" in hits[0]["memory"]
    assert "beta" in hits[1]["memory"]


def test_deduplication_threshold_filters_near_duplicate():
    candidates = [
        {"memory_id": "m1", "score": 0.99, "embedding": [1.0, 0.0]},
        {"memory_id": "m2", "score": 0.98, "embedding": [0.995, 0.1]},
        {"memory_id": "m3", "score": 0.97, "embedding": [0.0, 1.0]},
    ]
    strict = deduplicate_results(candidates, query_vector=[1.0, 0.0], top_k=2, threshold=0.89)
    assert [x["memory_id"] for x in strict] == ["m1", "m3"]


def test_dedup_prefers_novelty_under_high_threshold():
    candidates = [
        {"memory_id": "m1", "score": 1.0, "embedding": [1.0, 0.0, 0.0]},
        {"memory_id": "m2", "score": 0.99, "embedding": [0.9, 0.1, 0.0]},
        {"memory_id": "m3", "score": 0.6, "embedding": [0.0, 1.0, 0.0]},
    ]
    out = deduplicate_results(candidates, query_vector=[1.0, 0.0, 0.0], top_k=2, threshold=0.999)
    assert [x["memory_id"] for x in out] == ["m1", "m3"]

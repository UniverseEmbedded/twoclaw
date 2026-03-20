from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import mem1
from mem1.api import openai_proxy
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


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}
        self.content = openai_proxy.httpx.Response(status_code=200, json=payload).content

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    calls: list[dict[str, Any]] = []
    next_response: _FakeResponse | None = None

    def __init__(self, *args, **kwargs):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, headers: dict[str, str], json: dict[str, Any]):
        self.__class__.calls.append({"url": url, "headers": dict(headers), "json": dict(json)})
        if self.__class__.next_response is None:
            raise RuntimeError("missing_fake_response")
        return self.__class__.next_response


def test_chat_completions_injects_memory_and_writebacks(tmp_path: Path, monkeypatch):
    app = create_app(settings=_test_settings(tmp_path))
    client = TestClient(app)
    monkeypatch.setattr(openai_proxy.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.next_response = _FakeResponse(
        {
            "id": "chatcmpl-1",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "我记住了你的偏好"}}],
        }
    )

    add_resp = client.post(
        "/memories",
        json={
            "messages": [{"role": "user", "content": "我喜欢 Rust"}],
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
        },
    )
    assert add_resp.status_code == 200

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": "请结合我的偏好回答"},
            ],
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
            "memory": {"enabled": True, "writeback": True, "max_context_chars": 4000},
            "metadata": {"scene": "phase-d"},
        },
    )
    assert resp.status_code == 200
    assert _FakeAsyncClient.calls
    fwd = _FakeAsyncClient.calls[0]["json"]
    assert "user_id" not in fwd
    assert fwd["messages"][1]["role"] == "system"
    assert "[MEMORIES]" in str(fwd["messages"][1]["content"])

    mems = client.get("/memories", params={"user_id": "u1", "agent_id": "a1", "run_id": "r1", "limit": 10}).json()
    assert len(mems) >= 2
    assert any("我记住了你的偏好" in str(x.get("memory") or "") for x in mems)


def test_chat_completions_without_scope_is_passthrough(tmp_path: Path, monkeypatch):
    app = create_app(settings=_test_settings(tmp_path))
    client = TestClient(app)
    monkeypatch.setattr(openai_proxy.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.next_response = _FakeResponse(
        {
            "id": "chatcmpl-2",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "普通回答"}}],
        }
    )

    original_messages = [{"role": "user", "content": "你好"}]
    resp = client.post("/v1/chat/completions", json={"model": "auto", "messages": original_messages})
    assert resp.status_code == 200
    assert _FakeAsyncClient.calls
    fwd = _FakeAsyncClient.calls[0]["json"]
    assert fwd["messages"] == original_messages
    all_mems = client.get("/memories", params={"limit": 10})
    assert all_mems.status_code == 200
    assert all_mems.json() == []


def test_chat_completions_header_read_disables_writeback(tmp_path: Path, monkeypatch):
    app = create_app(settings=_test_settings(tmp_path))
    client = TestClient(app)
    monkeypatch.setattr(openai_proxy.httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.next_response = _FakeResponse(
        {
            "id": "chatcmpl-3",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "只读模式回答"}}],
        }
    )

    add_resp = client.post(
        "/memories",
        json={
            "messages": [{"role": "user", "content": "我住在上海"}],
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
        },
    )
    assert add_resp.status_code == 200
    before = client.get("/memories", params={"user_id": "u1", "agent_id": "a1", "run_id": "r1", "limit": 10}).json()

    resp = client.post(
        "/v1/chat/completions",
        headers={"X-Memory-Mode": "read"},
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "我住在哪里？"}],
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
        },
    )
    assert resp.status_code == 200
    fwd = _FakeAsyncClient.calls[0]["json"]
    assert "[MEMORIES]" in str(fwd["messages"][0]["content"])

    after = client.get("/memories", params={"user_id": "u1", "agent_id": "a1", "run_id": "r1", "limit": 10}).json()
    assert len(after) == len(before)

import json
import os
import multiprocessing as mp
from pathlib import Path
from typing import Sequence

from .base import Embedder


def _load_project_from_adc() -> str:
    cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or ""
    if not cred:
        return ""
    p = Path(cred)
    if not p.exists():
        return ""
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""
    pid = raw.get("project_id") if isinstance(raw, dict) else None
    return str(pid) if isinstance(pid, str) and pid else ""


def _build_genai_client():
    from google import genai

    vertexai = (os.environ.get("FREEPOOL_GEMINI_VERTEXAI") or "1").strip() == "1"
    api_key = os.environ.get("FREEPOOL_GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
    project = os.environ.get("FREEPOOL_GEMINI_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
    location = os.environ.get("FREEPOOL_GEMINI_LOCATION") or os.environ.get("GOOGLE_CLOUD_LOCATION") or ""

    if not project:
        project = _load_project_from_adc()

    if not api_key and not vertexai:
        raise RuntimeError("gemini_not_configured")

    kwargs: dict[str, object] = {"vertexai": vertexai}
    if api_key:
        kwargs["api_key"] = api_key
    if project:
        kwargs["project"] = project
    if location:
        kwargs["location"] = location

    try:
        return genai.Client(**kwargs)
    except TypeError:
        kwargs.pop("project", None)
        kwargs.pop("location", None)
        return genai.Client(**kwargs)

def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)

def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "y", "on"}


def _batch_size() -> int:
    n = _env_int("MEM1_GEMINI_BATCH_SIZE", 16)
    return max(1, min(int(n), 128))


def _resp_to_vectors(resp) -> list[list[float]]:
    emb = getattr(resp, "embedding", None)
    if emb is not None:
        vals = getattr(emb, "values", None)
        if vals is None:
            raise RuntimeError("gemini_embedding_empty")
        vec = [float(x) for x in vals]
        if not vec:
            raise RuntimeError("gemini_embedding_empty")
        return [vec]

    embs = getattr(resp, "embeddings", None)
    if not isinstance(embs, list) or not embs:
        raise RuntimeError("gemini_embedding_empty")
    out: list[list[float]] = []
    for e in embs:
        vals = getattr(e, "values", None)
        if vals is None:
            raise RuntimeError("gemini_embedding_empty")
        vec = [float(x) for x in vals]
        if not vec:
            raise RuntimeError("gemini_embedding_empty")
        out.append(vec)
    return out


def _embed_texts_worker(model: str, texts: list[str], q: "mp.Queue") -> None:
    try:
        client = _build_genai_client()
        out: list[list[float]] = []
        bs = _batch_size()
        for i in range(0, len(texts), bs):
            batch = [str(x or "") for x in texts[i : i + bs]]
            resp = client.models.embed_content(model=model, contents=batch)
            out.extend(_resp_to_vectors(resp))
        q.put({"vectors": out})
    except Exception as e:
        q.put({"error": str(e)})


def _embed_texts_with_timeout(*, model: str, texts: Sequence[str], timeout_s: int) -> list[list[float]]:
    ctx = mp.get_context("spawn")
    q: "mp.Queue" = ctx.Queue(maxsize=1)
    p = ctx.Process(target=_embed_texts_worker, args=(model, [str(x or "") for x in texts], q), daemon=True)
    p.start()
    p.join(max(1, int(timeout_s)))
    if p.is_alive():
        p.terminate()
        p.join(2)
        raise TimeoutError("gemini_embedding_timeout")
    try:
        msg = q.get_nowait()
    except Exception:
        raise RuntimeError("gemini_embedding_worker_no_result")
    if isinstance(msg, dict) and "error" in msg:
        raise RuntimeError(str(msg.get("error") or "gemini_embedding_error"))
    vecs = msg.get("vectors") if isinstance(msg, dict) else None
    if not isinstance(vecs, list) or not vecs:
        raise RuntimeError("gemini_embedding_empty")
    return vecs

class GeminiEmbedder(Embedder):
    def __init__(self, *, model: str = "gemini-embedding-001"):
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        timeout_s = _env_int("MEM1_EMBED_TIMEOUT_SECS", 0)
        if timeout_s > 0 and _env_bool("MEM1_ENABLE_MP_TIMEOUT", False):
            return _embed_texts_with_timeout(model=self._model, texts=texts, timeout_s=timeout_s)

        client = _build_genai_client()
        out: list[list[float]] = []
        bs = _batch_size()
        for i in range(0, len(texts), bs):
            batch = [str(x or "") for x in texts[i : i + bs]]
            resp = client.models.embed_content(model=self._model, contents=batch)
            out.extend(_resp_to_vectors(resp))
        return out

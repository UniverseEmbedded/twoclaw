import json
import os
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


class GeminiEmbedder(Embedder):
    def __init__(self, *, model: str = "gemini-embedding-001"):
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        client = _build_genai_client()
        out: list[list[float]] = []
        for t in texts:
            resp = client.models.embed_content(model=self._model, contents=str(t or ""))
            emb = getattr(resp, "embedding", None)
            if emb is None:
                embs = getattr(resp, "embeddings", None)
                if isinstance(embs, list) and embs:
                    emb = embs[0]
            vals = getattr(emb, "values", None) if emb is not None else None
            if vals is None:
                raise RuntimeError("gemini_embedding_empty")
            try:
                vec = [float(x) for x in vals]
            except Exception as e:
                raise RuntimeError("gemini_embedding_invalid") from e
            if not vec:
                raise RuntimeError("gemini_embedding_empty")
            out.append(vec)
        return out

import hashlib
from typing import Sequence

from .base import Embedder


def _hash_to_floats(text: str, *, dim: int) -> list[float]:
    h = hashlib.sha256(text.encode("utf-8")).digest()
    out: list[float] = []
    i = 0
    while len(out) < dim:
        b = h[i % len(h)]
        out.append((b / 255.0) * 2.0 - 1.0)
        i += 1
    return out


class HashEmbedder(Embedder):
    def __init__(self, *, model: str = "hash-v1", dim: int = 64):
        self._model = model
        self._dim = dim

    @property
    def model_name(self) -> str:
        return self._model

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [_hash_to_floats(t or "", dim=self._dim) for t in texts]

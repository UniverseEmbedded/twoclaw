from abc import ABC, abstractmethod
from typing import Sequence


class Embedder(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

from .chunker import chunk_text
from .dedup import deduplicate_results
from .similarity import cosine_similarity

__all__ = ["chunk_text", "cosine_similarity", "deduplicate_results"]

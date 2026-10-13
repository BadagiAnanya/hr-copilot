"""Local embedding model. Loaded once per process."""
from __future__ import annotations
from functools import lru_cache
from sentence_transformers import SentenceTransformer
import numpy as np
from ..config import get_settings


@lru_cache(maxsize=1)
def model() -> SentenceTransformer:
    s = get_settings()
    return SentenceTransformer(s.embedding_model)


def embed(texts: list[str]) -> np.ndarray:
    """Return a (n, dim) array of normalized embeddings."""
    return model().encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )


def embed_one(text: str) -> list[float]:
    return embed([text])[0].tolist()

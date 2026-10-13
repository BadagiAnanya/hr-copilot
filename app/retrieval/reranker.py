"""Cross-encoder reranker. Big quality lift for ~200ms."""
from __future__ import annotations
from functools import lru_cache
from sentence_transformers import CrossEncoder
from ..config import get_settings
from ..types import RetrievedChunk


@lru_cache(maxsize=1)
def model() -> CrossEncoder:
    s = get_settings()
    return CrossEncoder(s.reranker_model)


def rerank(query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    if not chunks:
        return []
    pairs = [(query, c.content) for c in chunks]
    scores = model().predict(pairs)
    # Replace fused score with reranker score so downstream sees calibrated numbers.
    rescored = []
    for chunk, score in zip(chunks, scores):
        chunk.score = float(score)
        rescored.append(chunk)
    rescored.sort(key=lambda c: c.score, reverse=True)
    return rescored[:top_k]

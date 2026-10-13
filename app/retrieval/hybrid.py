"""Top-level retrieve(): embed → hybrid search → rerank → threshold."""
from __future__ import annotations
from ..config import get_settings
from ..types import RetrievedChunk, UserContext
from . import embeddings, reranker, store


async def retrieve(query: str, user: UserContext) -> list[RetrievedChunk]:
    s = get_settings()
    q_emb = embeddings.embed_one(query)
    coarse = await store.hybrid_search(
        query_text=query,
        query_embedding=q_emb,
        acl_groups=user.acl_groups,
        top_k=s.retrieval_top_k,
    )
    if not coarse:
        return []
    fine = reranker.rerank(query, coarse, top_k=s.rerank_top_k)
    # Drop chunks below the confidence threshold — this feeds abstention.
    return [c for c in fine if c.score >= s.retrieval_score_threshold]

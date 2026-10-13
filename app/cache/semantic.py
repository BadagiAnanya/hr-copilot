"""L3 semantic answer cache.

Lookup: embed the question, find nearest past question with the SAME ACL hash,
return the stored answer if cosine >= threshold. ACL hash prevents cross-tenant
leaks.
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict
from typing import Any, Optional
import numpy as np
import redis.asyncio as redis
from ..config import get_settings
from ..retrieval.embeddings import embed_one
from ..types import Citation


_SET_KEY = "cache:l3:keys"
_PREFIX = "cache:l3:item:"


def _acl_hash(acl_groups: list[str]) -> str:
    h = hashlib.sha256("|".join(sorted(acl_groups)).encode()).hexdigest()[:12]
    return h


async def _r() -> redis.Redis:
    s = get_settings()
    return redis.from_url(s.redis_url, decode_responses=True)


async def lookup(question: str, acl_groups: list[str]) -> Optional[dict[str, Any]]:
    """Linear scan over cached (vector, item). Fine up to ~10k items; swap to
    Redis-Search HNSW if the cache grows beyond that."""
    s = get_settings()
    r = await _r()
    try:
        acl_h = _acl_hash(acl_groups)
        q_emb = np.array(embed_one(question), dtype=np.float32)

        keys = await r.smembers(f"{_SET_KEY}:{acl_h}")
        if not keys:
            return None

        best_item = None
        best_sim = -1.0
        for k in keys:
            raw = await r.get(_PREFIX + k)
            if not raw:
                continue
            item = json.loads(raw)
            v = np.array(item["embedding"], dtype=np.float32)
            # Both vectors are L2-normalized → dot product == cosine.
            sim = float(np.dot(q_emb, v))
            if sim > best_sim:
                best_sim = sim
                best_item = item

        if best_item and best_sim >= s.semantic_cache_threshold:
            return {
                "answer": best_item["answer"],
                "citations": [Citation(**c) for c in best_item["citations"]],
                "similarity": best_sim,
            }
        return None
    finally:
        await r.close()


async def store(question: str, acl_groups: list[str], answer: str, citations: list[Citation]) -> None:
    r = await _r()
    try:
        acl_h = _acl_hash(acl_groups)
        key = hashlib.sha256(f"{acl_h}|{question}".encode()).hexdigest()[:16]
        item = {
            "question": question,
            "embedding": embed_one(question),
            "answer": answer,
            "citations": [asdict(c) for c in citations],
        }
        await r.set(_PREFIX + key, json.dumps(item), ex=60 * 60 * 24 * 7)  # 7d
        await r.sadd(f"{_SET_KEY}:{acl_h}", key)
    finally:
        await r.close()


async def invalidate_for_documents(document_ids: list[str]) -> None:
    """Called by ingestion when docs change. Blunt but correct: nuke the cache."""
    r = await _r()
    try:
        async for key in r.scan_iter(_PREFIX + "*"):
            raw = await r.get(key)
            if not raw:
                continue
            item = json.loads(raw)
            if any(c["document_id"] in document_ids for c in item.get("citations", [])):
                await r.delete(key)
    finally:
        await r.close()

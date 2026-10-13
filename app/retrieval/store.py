"""Postgres queries for hybrid retrieval.

ACL is filtered at query time via acl_groups && user_groups.
Hybrid score = Reciprocal Rank Fusion (RRF) of dense + BM25 ranks.
RRF is simpler and more robust than tuned score-weighting.
"""
from __future__ import annotations
import psycopg
from psycopg.rows import dict_row
from ..config import get_settings
from ..types import RetrievedChunk


# RRF constant. 60 is the common choice from the original paper.
RRF_K = 60


async def connect() -> psycopg.AsyncConnection:
    s = get_settings()
    return await psycopg.AsyncConnection.connect(s.postgres_url, row_factory=dict_row)


async def hybrid_search(
    query_text: str,
    query_embedding: list[float],
    acl_groups: list[str],
    top_k: int,
) -> list[RetrievedChunk]:
    """
    One SQL round-trip: dense top-N + BM25 top-N, fused by RRF, ACL pre-filtered.

    Keeping dense and BM25 as CTEs makes the query readable and lets the planner
    use each index independently.
    """
    s = get_settings()
    emb_literal = "[" + ",".join(f"{x:.6f}" for x in query_embedding) + "]"

    sql = f"""
    WITH dense AS (
        SELECT c.id, c.document_id, c.section, c.content,
               ROW_NUMBER() OVER (ORDER BY c.embedding <=> %s::vector) AS rank
        FROM chunks c
        WHERE c.acl_groups && %s
        ORDER BY c.embedding <=> %s::vector
        LIMIT {top_k * 2}
    ),
    bm25 AS (
        SELECT c.id, c.document_id, c.section, c.content,
               ROW_NUMBER() OVER (
                   ORDER BY ts_rank_cd(c.tsv, plainto_tsquery('english', %s)) DESC
               ) AS rank
        FROM chunks c
        WHERE c.acl_groups && %s
          AND c.tsv @@ plainto_tsquery('english', %s)
        LIMIT {top_k * 2}
    ),
    fused AS (
        SELECT COALESCE(d.id, b.id) AS id,
               COALESCE(d.document_id, b.document_id) AS document_id,
               COALESCE(d.section, b.section) AS section,
               COALESCE(d.content, b.content) AS content,
               COALESCE(1.0 / ({RRF_K} + d.rank), 0) +
               COALESCE(1.0 / ({RRF_K} + b.rank), 0) AS fused_score
        FROM dense d
        FULL OUTER JOIN bm25 b USING (id, document_id, section, content)
    )
    SELECT f.*, d.source_url
    FROM fused f
    LEFT JOIN documents d ON d.id = f.document_id
    ORDER BY fused_score DESC
    LIMIT %s;
    """

    async with await connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                sql,
                (emb_literal, acl_groups, emb_literal,
                 query_text, acl_groups, query_text,
                 top_k),
            )
            rows = await cur.fetchall()

    return [
        RetrievedChunk(
            id=r["id"],
            document_id=r["document_id"],
            section=r["section"],
            content=r["content"],
            score=float(r["fused_score"]),
            source_url=r.get("source_url"),
        )
        for r in rows
    ]

"""Ingestion runner. Shaped like an Airflow DAG but runnable as a script.

Steps:
  1. Scan data/policies/*.md
  2. For each doc: hash → skip if unchanged, else re-chunk → embed → upsert
  3. Invalidate semantic cache for touched docs
  4. Run data-quality checks
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
import psycopg
from ..cache.semantic import invalidate_for_documents
from ..config import get_settings
from ..retrieval.embeddings import embed
from .chunker import chunk_markdown


DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"


async def ingest_all() -> dict:
    s = get_settings()
    touched: list[str] = []
    stats = {"docs_seen": 0, "docs_updated": 0, "chunks": 0}

    files = sorted(DATA_DIR.glob("*.md"))
    if not files:
        print(f"No .md files in {DATA_DIR}", file=sys.stderr)
        return stats

    for path in files:
        stats["docs_seen"] += 1
        text = path.read_text()
        header = _parse_frontmatter(text)
        doc_id = header.get("id", path.stem)
        content = _strip_frontmatter(text)
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        async with await psycopg.AsyncConnection.connect(s.postgres_url) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT content_hash FROM documents WHERE id=%s", (doc_id,)
                )
                existing = await cur.fetchone()
                if existing and existing[0] == content_hash:
                    print(f"  = {doc_id} (unchanged)")
                    continue

                print(f"  * {doc_id} (ingesting)")
                stats["docs_updated"] += 1
                touched.append(doc_id)

                chunks = chunk_markdown(content)
                vectors = embed([c.content for c in chunks])
                acl = header.get("acl_groups", ["all_employees"])
                dept = header.get("department")
                eff = header.get("effective_date")

                await cur.execute("DELETE FROM chunks WHERE document_id=%s", (doc_id,))
                await cur.execute(
                    """INSERT INTO documents (id, title, source_url, version, effective_date, content_hash)
                       VALUES (%s, %s, %s, 1, %s, %s)
                       ON CONFLICT (id) DO UPDATE
                       SET title=EXCLUDED.title, source_url=EXCLUDED.source_url,
                           version=documents.version+1, content_hash=EXCLUDED.content_hash,
                           ingested_at=NOW()""",
                    (doc_id, header.get("title", doc_id), header.get("source_url"),
                     eff, content_hash),
                )
                for chunk, vec in zip(chunks, vectors):
                    vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
                    await cur.execute(
                        """INSERT INTO chunks
                           (document_id, section, content, embedding, acl_groups, department, effective_date)
                           VALUES (%s,%s,%s,%s::vector,%s,%s,%s)""",
                        (doc_id, chunk.section, chunk.content, vec_literal, acl, dept, eff),
                    )
                    stats["chunks"] += 1
                await conn.commit()

    if touched:
        await invalidate_for_documents(touched)

    # Quality checks (Great-Expectations-shaped, but inline for simplicity)
    issues = await _quality_checks()
    stats["quality_issues"] = issues
    print(json.dumps(stats, indent=2, default=str))
    return stats


def _parse_frontmatter(text: str) -> dict:
    """Minimal YAML-ish frontmatter: --- ... --- at the top."""
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    out: dict = {}
    for line in text[3:end].strip().splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            out[k.strip()] = [x.strip().strip('"') for x in v[1:-1].split(",") if x.strip()]
        else:
            out[k.strip()] = v.strip('"')
    return out


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("---", 3)
    return text[end + 3:].lstrip() if end != -1 else text


async def _quality_checks() -> list[str]:
    s = get_settings()
    issues: list[str] = []
    async with await psycopg.AsyncConnection.connect(s.postgres_url) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM chunks WHERE content IS NULL OR content=''")
            empty = (await cur.fetchone())[0]
            if empty:
                issues.append(f"{empty} empty chunks")
            await cur.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL")
            no_emb = (await cur.fetchone())[0]
            if no_emb:
                issues.append(f"{no_emb} chunks missing embeddings")
    return issues


if __name__ == "__main__":
    asyncio.run(ingest_all())

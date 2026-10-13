"""Append-only audit log. One row per completed question."""
from __future__ import annotations
import json
from dataclasses import asdict
import psycopg
from ..config import get_settings
from ..types import Trace


async def record(trace: Trace, feedback: str | None = None) -> None:
    s = get_settings()
    async with await psycopg.AsyncConnection.connect(s.postgres_url) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO audit_log
                   (trace_id, slack_user_id, employee_id, question, answer,
                    retrieved_ids, citations, tool_calls, route, latency_ms, feedback)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)""",
                (
                    trace.trace_id,
                    trace.user.slack_user_id,
                    trace.user.employee_id,
                    trace.question,
                    trace.answer,
                    [c.id for c in trace.retrieved],
                    json.dumps([asdict(c) for c in trace.citations]),
                    json.dumps([asdict(tc) for tc in trace.tool_calls], default=str),
                    trace.route,
                    trace.latency_ms,
                    feedback,
                ),
            )
            await conn.commit()

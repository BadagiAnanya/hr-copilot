"""Mock HRMS tools backed by Postgres.

Also exposes `pick_tool_for(question, user)` — a cheap keyword-based selector.
If this misfires in evals, swap in an LLM-based selector; keep the heuristic
as a fast path.
"""
from __future__ import annotations
import re
from datetime import date
from typing import Any, Optional
from ..config import get_settings
from ..types import UserContext


def _pg():
    """Lazy import so tests of pure helpers (pick_tool_for) don't need psycopg."""
    import psycopg
    from psycopg.rows import dict_row
    return psycopg, dict_row


# ---------- Tool implementations ----------

async def get_leave_balance(employee_id: str, year: Optional[int] = None) -> dict:
    year = year or date.today().year
    s = get_settings()
    psycopg, dict_row = _pg()
    async with await psycopg.AsyncConnection.connect(
        s.postgres_url, row_factory=dict_row
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT leave_type, allotted, used, (allotted - used) AS remaining
                   FROM leave_balances WHERE employee_id=%s AND year=%s""",
                (employee_id, year),
            )
            rows = await cur.fetchall()
    return {"employee_id": employee_id, "year": year, "balances": rows}


async def get_employee_profile(employee_id: str) -> dict:
    s = get_settings()
    psycopg, dict_row = _pg()
    async with await psycopg.AsyncConnection.connect(
        s.postgres_url, row_factory=dict_row
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT employee_id, name, email, department, location,
                          manager_id, joined_at
                   FROM employees WHERE employee_id=%s""",
                (employee_id,),
            )
            row = await cur.fetchone()
    if not row:
        raise ValueError(f"Employee not found: {employee_id}")
    return row


async def create_hr_ticket(employee_id: str, category: str, body: str) -> dict:
    # Mock: in production this would hit the ticketing system.
    ticket_id = f"HR-{abs(hash((employee_id, body))) % 100000}"
    return {"ticket_id": ticket_id, "status": "open", "category": category}


TOOLS: dict[str, Any] = {
    "get_leave_balance": get_leave_balance,
    "get_employee_profile": get_employee_profile,
    "create_hr_ticket": create_hr_ticket,
}


async def call_tool(name: str, args: dict) -> Any:
    if name not in TOOLS:
        raise ValueError(f"Unknown tool: {name}")
    return await TOOLS[name](**args)


# ---------- Deterministic tool selection ----------

_LEAVE_HINTS = re.compile(r"\b(leave|leaves|balance|remaining|vacation|pto|holiday)\b", re.I)
_PROFILE_HINTS = re.compile(r"\b(join(ed)?|start(ed)?|manager|department|team|tenure)\b", re.I)
_TICKET_HINTS = re.compile(r"\b(ticket|raise|complaint|issue|report)\b", re.I)


def pick_tool_for(question: str, user: UserContext) -> tuple[Optional[str], dict]:
    """Return (tool_name, args) or (None, {}) if nothing fits."""
    if not user.employee_id:
        return None, {}
    if _LEAVE_HINTS.search(question):
        return "get_leave_balance", {"employee_id": user.employee_id}
    if _PROFILE_HINTS.search(question):
        return "get_employee_profile", {"employee_id": user.employee_id}
    if _TICKET_HINTS.search(question):
        return "create_hr_ticket", {
            "employee_id": user.employee_id,
            "category": "general",
            "body": question,
        }
    return None, {}

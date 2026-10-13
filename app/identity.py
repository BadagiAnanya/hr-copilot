"""Map a Slack user_id to our employee record + ACL groups."""
from __future__ import annotations
import psycopg
from psycopg.rows import dict_row
from .config import get_settings
from .types import UserContext


async def resolve(slack_user_id: str) -> UserContext:
    s = get_settings()
    async with await psycopg.AsyncConnection.connect(
        s.postgres_url, row_factory=dict_row
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM employees WHERE slack_user_id=%s",
                (s.slack_user_aliases.get(slack_user_id, slack_user_id),),
            )
            row = await cur.fetchone()
    if not row:
        # Unknown user: minimal ACL, no employee context. Still lets them ask
        # public policy questions but blocks tool calls.
        return UserContext(slack_user_id=slack_user_id, acl_groups=["all_employees"])
    return UserContext(
        slack_user_id=slack_user_id,
        employee_id=row["employee_id"],
        name=row["name"],
        email=row["email"],
        department=row["department"],
        location=row["location"],
        acl_groups=list(row["acl_groups"]),
    )

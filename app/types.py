"""Shared data types across the pipeline. Keep these small and serializable."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional
import uuid

Route = Literal["policy", "tool", "compound", "sensitive", "out_of_scope"]


@dataclass
class UserContext:
    """Who's asking, and what they're allowed to see."""
    slack_user_id: str
    employee_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    acl_groups: list[str] = field(default_factory=lambda: ["all_employees"])


@dataclass
class RetrievedChunk:
    id: int
    document_id: str
    section: Optional[str]
    content: str
    score: float
    source_url: Optional[str] = None


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: Any = None
    error: Optional[str] = None
    latency_ms: int = 0


@dataclass
class Citation:
    document_id: str
    section: Optional[str]
    chunk_id: int
    source_url: Optional[str] = None


@dataclass
class Trace:
    """Everything that happened for one question. Used for audit + evals."""
    trace_id: str
    user: UserContext
    question: str
    route: Optional[Route] = None
    retrieved: list[RetrievedChunk] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    abstained: bool = False
    escalated: bool = False
    cache_hit: Optional[str] = None  # 'l1' | 'l2' | 'l3' | None
    latency_ms: int = 0
    cost_usd: float = 0.0
    started_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def new(cls, user: UserContext, question: str) -> "Trace":
        return cls(trace_id=str(uuid.uuid4()), user=user, question=question)


@dataclass
class Job:
    """What goes on the queue."""
    trace_id: str
    slack_user_id: str
    channel_id: str
    thread_ts: str          # placeholder message ts, to update later
    text: str
    received_at: datetime

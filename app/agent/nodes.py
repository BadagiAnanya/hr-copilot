"""Individual nodes used by the agent graph.

Each node takes a Trace and mutates it. Keeping them as plain async functions
(rather than LangGraph nodes) makes them trivial to unit-test in isolation.
"""
from __future__ import annotations
import re
from typing import Any

from ..types import Citation, Trace, ToolCall
from .prompts import (
    ESCALATION_MESSAGE,
    GENERATOR_SYSTEM,
    OUT_OF_SCOPE_MESSAGE,
)

# Heavy modules (sentence-transformers, groq) are imported inside the nodes
# that need them so that unit tests covering pure logic (guardrails, citation
# parsing) can run without the ML stack installed.

# gpt-oss models often emit 【doc#section】 instead of [doc#section]; accept both.
CITATION_RE = re.compile(r"[\[【]([a-z0-9_]+)(?:#([^\]】]+))?[\]】]", re.IGNORECASE)
PII_PATTERNS = [
    # Indian PAN (lenient), Aadhaar-like 12-digit runs, long card-like runs
    re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
    re.compile(r"\b\d{12}\b"),
    re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
]


# ---------- Retrieval node ----------

async def do_retrieve(trace: Trace) -> None:
    from ..retrieval.hybrid import retrieve
    trace.retrieved = await retrieve(trace.question, trace.user)


# ---------- Tool node ----------

async def do_tool(trace: Trace) -> None:
    """
    Pick a tool deterministically from a small whitelist first; fall back to
    an LLM selector only when the heuristic can't decide. Heuristics are
    faster, cheaper, and far easier to eval than letting the model pick blindly.
    """
    from ..tools.hrms import call_tool, pick_tool_for
    tool_name, args = pick_tool_for(trace.question, trace.user)
    if tool_name is None:
        return
    tc = ToolCall(name=tool_name, args=args)
    try:
        tc.result = await call_tool(tool_name, args)
    except Exception as e:  # noqa: BLE001
        tc.error = str(e)
    trace.tool_calls.append(tc)


# ---------- Generator node ----------

def _format_context(trace: Trace) -> str:
    lines = []
    for c in trace.retrieved:
        tag = f"[{c.document_id}#{c.section or 'main'}]"
        lines.append(f"{tag}\n{c.content.strip()}\n")
    return "\n---\n".join(lines) if lines else "(no documents retrieved)"


def _format_tool_results(trace: Trace) -> str:
    if not trace.tool_calls:
        return "(no tool results)"
    out = []
    for tc in trace.tool_calls:
        if tc.error:
            out.append(f"TOOL {tc.name} FAILED: {tc.error}")
        else:
            out.append(f"TOOL {tc.name}({tc.args}) = {tc.result}")
    return "\n".join(out)


async def do_generate(trace: Trace) -> None:
    """Produce the final answer with inline citations."""
    from ..llm import complete
    user_msg = (
        f"USER_QUESTION: {trace.question}\n\n"
        f"USER: {trace.user.name or trace.user.slack_user_id} "
        f"(dept={trace.user.department}, loc={trace.user.location})\n\n"
        f"CONTEXT:\n{_format_context(trace)}\n\n"
        f"TOOL_RESULTS:\n{_format_tool_results(trace)}\n\n"
        f"Write the answer now."
    )
    trace.answer = await complete(
        GENERATOR_SYSTEM, user_msg, temperature=0.1, max_tokens=400
    )
    trace.citations = _extract_citations(trace)


def _extract_citations(trace: Trace) -> list[Citation]:
    """Parse [doc_id#section] markers from the answer; keep only ones that correspond
    to actually-retrieved chunks (prevents hallucinated citations)."""
    retrieved_by_key: dict[tuple[str, str], Any] = {}
    for c in trace.retrieved:
        retrieved_by_key[(c.document_id, (c.section or "main"))] = c

    cites: list[Citation] = []
    seen: set[tuple[str, str]] = set()
    for m in CITATION_RE.finditer(trace.answer):
        doc_id = m.group(1).lower()
        section = (m.group(2) or "main").strip()
        key = (doc_id, section)
        if key in seen:
            continue
        chunk = retrieved_by_key.get(key)
        if not chunk:
            # unknown citation — strip it later; don't record it
            continue
        seen.add(key)
        cites.append(Citation(
            document_id=doc_id,
            section=None if section == "main" else section,
            chunk_id=chunk.id,
            source_url=chunk.source_url,
        ))
    return cites


# ---------- Guardrails ----------

def do_guardrails(trace: Trace) -> None:
    """Strip PII, enforce abstention when retrieval was empty or weak."""
    if not trace.retrieved and not trace.tool_calls and not trace.escalated:
        trace.answer = (
            "I don't have a confident answer for that — want me to route it to HR?"
        )
        trace.abstained = True
        return

    # PII scrub (defensive — the generator shouldn't produce these, but belt and braces)
    for p in PII_PATTERNS:
        trace.answer = p.sub("[REDACTED]", trace.answer)


# ---------- Terminal messages ----------

def do_sensitive(trace: Trace) -> None:
    trace.answer = ESCALATION_MESSAGE
    trace.escalated = True


def do_out_of_scope(trace: Trace) -> None:
    trace.answer = OUT_OF_SCOPE_MESSAGE

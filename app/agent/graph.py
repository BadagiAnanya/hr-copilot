"""The agent as a small state machine.

Written as plain async code (not a LangGraph DSL) for clarity + testability.
Each branch is linear; the only decision point is the router. This is the
right level of complexity for HR FAQ — a full agentic loop is unnecessary
and much harder to eval.
"""
from __future__ import annotations
import asyncio
import time
from ..cache.semantic import lookup as cache_lookup, store as cache_store
from ..observability.tracing import span
from ..types import Trace, UserContext
from . import nodes, router


async def answer(user: UserContext, question: str) -> Trace:
    trace = Trace.new(user=user, question=question)
    t0 = time.monotonic()

    # L3 semantic cache first — Zipfian FAQ traffic benefits hugely.
    with span("cache.semantic"):
        cached = await cache_lookup(question, user.acl_groups)
        if cached is not None:
            trace.answer = cached["answer"]
            trace.citations = cached["citations"]
            trace.cache_hit = "l3"
            trace.latency_ms = int((time.monotonic() - t0) * 1000)
            return trace

    with span("router"):
        trace.route, _reason = await router.classify(question)

    if trace.route == "sensitive":
        nodes.do_sensitive(trace)
    elif trace.route == "out_of_scope":
        nodes.do_out_of_scope(trace)
    elif trace.route == "tool":
        with span("tool"):
            await nodes.do_tool(trace)
        with span("generate"):
            await nodes.do_generate(trace)
        nodes.do_guardrails(trace)
    elif trace.route == "compound":
        with span("tool+retrieve"):
            await asyncio.gather(nodes.do_tool(trace), nodes.do_retrieve(trace))
        with span("generate"):
            await nodes.do_generate(trace)
        nodes.do_guardrails(trace)
    else:  # policy
        with span("retrieve"):
            await nodes.do_retrieve(trace)
        with span("generate"):
            await nodes.do_generate(trace)
        nodes.do_guardrails(trace)

    trace.latency_ms = int((time.monotonic() - t0) * 1000)

    # Cache only confident, cite-backed, non-escalated policy answers.
    if (
        trace.route == "policy"
        and not trace.abstained
        and trace.citations
    ):
        await cache_store(question, user.acl_groups, trace.answer, trace.citations)

    return trace

"""Thin span wrapper. Logs structured timing; integrates with Langfuse if configured."""
from __future__ import annotations
import contextlib
import time
import structlog

log = structlog.get_logger("trace")


@contextlib.contextmanager
def span(name: str):
    t0 = time.monotonic()
    try:
        yield
    finally:
        dt = int((time.monotonic() - t0) * 1000)
        log.info("span", name=name, latency_ms=dt)

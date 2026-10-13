"""Worker coroutines that drain the queue."""
from __future__ import annotations
import asyncio
import structlog
from .agent.graph import answer as run_agent
from .config import get_settings
from .identity import resolve
from .observability.audit import record
from .slack.bolt_app import get_app, get_queue
from .slack.formatting import answer_blocks, display_text
from .types import Job

log = structlog.get_logger("worker")


async def _handle(job: Job) -> None:
    try:
        user = await resolve(job.slack_user_id)
        trace = await run_agent(user, job.text)
        app = get_app()
        await app.client.chat_update(
            channel=job.channel_id,
            ts=job.thread_ts,
            blocks=answer_blocks(trace),
            text=display_text(trace)[:200],
        )
        await record(trace)
    except Exception as e:  # noqa: BLE001
        log.exception("worker.failed", trace_id=job.trace_id, error=str(e))
        try:
            await get_app().client.chat_update(
                channel=job.channel_id,
                ts=job.thread_ts,
                text=":warning: Something went wrong. Please try again.",
            )
        except Exception:
            pass


async def run_workers() -> None:
    s = get_settings()
    q = get_queue()

    async def loop():
        while True:
            job = await q.get()
            try:
                await _handle(job)
            finally:
                q.task_done()

    log.info("worker.start", concurrency=s.worker_concurrency)
    await asyncio.gather(*(loop() for _ in range(s.worker_concurrency)))

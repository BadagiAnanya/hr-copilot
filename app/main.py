"""FastAPI entrypoint. Serves Slack webhooks and runs the worker pool.

If SLACK_APP_TOKEN is set, also connects over Socket Mode, so local dev
needs no public URL or tunnel.

Run:  uvicorn app.main:app --reload --port 3000
"""
from __future__ import annotations
import asyncio
import logging
from contextlib import asynccontextmanager
import structlog
from fastapi import FastAPI, Request
from .config import get_settings
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from .slack.bolt_app import get_app, get_handler
from .worker import run_workers


def _configure_logging() -> None:
    s = get_settings()
    logging.basicConfig(level=s.log_level)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    worker_task = asyncio.create_task(run_workers())
    socket_handler = None
    s = get_settings()
    if s.slack_app_token:
        socket_handler = AsyncSocketModeHandler(get_app(), s.slack_app_token)
        await socket_handler.connect_async()
    try:
        yield
    finally:
        worker_task.cancel()
        if socket_handler:
            await socket_handler.close_async()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/slack/events")
async def slack_events(req: Request):
    return await get_handler().handle(req)


@app.post("/slack/commands")
async def slack_commands(req: Request):
    return await get_handler().handle(req)


@app.post("/slack/actions")
async def slack_actions(req: Request):
    return await get_handler().handle(req)

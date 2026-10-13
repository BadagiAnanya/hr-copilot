"""Slack Bolt app. Handles DMs, mentions, slash command, feedback buttons.

Slack's 3-second ack rule drives everything: we immediately post a placeholder
and enqueue the job. The worker updates the placeholder when the answer is ready.
"""
from __future__ import annotations
import asyncio
from datetime import datetime
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from ..config import get_settings
from ..types import Job
from .formatting import placeholder_blocks

settings = get_settings()
_app: AsyncApp | None = None
_queue: asyncio.Queue[Job] | None = None


def get_app() -> AsyncApp:
    global _app
    if _app is None:
        _app = AsyncApp(
            token=settings.slack_bot_token,
            signing_secret=settings.slack_signing_secret,
        )
        _register_handlers(_app)
    return _app


def get_queue() -> asyncio.Queue[Job]:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=1000)
    return _queue


def get_handler() -> AsyncSlackRequestHandler:
    return AsyncSlackRequestHandler(get_app())


def _register_handlers(app: AsyncApp) -> None:
    @app.event("app_mention")
    async def on_mention(event, say, client):
        await _enqueue(event, client, strip_mention=True)

    @app.event("message")
    async def on_message(event, say, client):
        # DMs only; ignore bot messages and channel chatter.
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        await _enqueue(event, client, strip_mention=False)

    @app.command("/ask")
    async def on_slash(ack, command, client):
        await ack()
        posted = await client.chat_postMessage(
            channel=command["channel_id"],
            blocks=placeholder_blocks(),
            text="Looking into this...",
        )
        await get_queue().put(Job(
            trace_id=posted["ts"],
            slack_user_id=command["user_id"],
            channel_id=command["channel_id"],
            thread_ts=posted["ts"],
            text=command["text"],
            received_at=datetime.utcnow(),
        ))

    @app.action("fb_up")
    async def fb_up(ack, body):
        await ack()
        # TODO: write feedback to audit_log

    @app.action("fb_down")
    async def fb_down(ack, body):
        await ack()
        # TODO

    @app.action("escalate")
    async def escalate(ack, body, client):
        await ack()
        await client.chat_postMessage(
            channel=body["channel"]["id"],
            text=":wave: Flagged for HR. Someone will be in touch shortly.",
            thread_ts=body["message"]["ts"],
        )


async def _enqueue(event, client, *, strip_mention: bool) -> None:
    text = event["text"]
    if strip_mention:
        # remove leading <@BOT_ID>
        text = " ".join(w for w in text.split() if not w.startswith("<@"))
    posted = await client.chat_postMessage(
        channel=event["channel"],
        blocks=placeholder_blocks(),
        text="Looking into this...",
        thread_ts=event.get("thread_ts") or event["ts"],
    )
    await get_queue().put(Job(
        trace_id=posted["ts"],
        slack_user_id=event["user"],
        channel_id=event["channel"],
        thread_ts=posted["ts"],
        text=text.strip(),
        received_at=datetime.utcnow(),
    ))

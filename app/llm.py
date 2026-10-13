"""Groq client wrapper. One place to swap providers, mock, or cache."""
from __future__ import annotations
import asyncio
import json
from typing import Any, Optional
from groq import AsyncGroq
from .config import get_settings

_client: Optional[AsyncGroq] = None


def client() -> AsyncGroq:
    global _client
    if _client is None:
        s = get_settings()
        _client = AsyncGroq(api_key=s.groq_api_key)
    return _client


async def complete(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    temperature: float = 0.0,
    max_tokens: int = 1024,
    json_mode: bool = False,
) -> str:
    """One-shot chat completion. Returns raw text."""
    s = get_settings()
    model = model or s.generator_model
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    # gpt-oss models reason before answering; keep it short so small
    # max_tokens budgets (router, judges) aren't spent before the answer.
    if "gpt-oss" in model:
        kwargs["extra_body"] = {"reasoning_effort": "low"}

    # Lightweight retry on rate limits — Groq free tier bursts.
    for attempt in range(3):
        try:
            resp = await client().chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "rate" in msg and attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("LLM retries exhausted")


async def complete_json(system: str, user: str, **kwargs) -> dict:
    """Return parsed JSON. Raises on invalid JSON."""
    raw = await complete(system, user, json_mode=True, **kwargs)
    return json.loads(raw)

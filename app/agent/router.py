"""Intent classifier using the small LLM."""
from __future__ import annotations
from typing import cast
from ..config import get_settings
from ..llm import complete_json
from ..types import Route
from .prompts import ROUTER_SYSTEM

_VALID: tuple[Route, ...] = ("policy", "tool", "compound", "sensitive", "out_of_scope")


async def classify(question: str) -> tuple[Route, str]:
    s = get_settings()
    try:
        out = await complete_json(
            ROUTER_SYSTEM,
            f"QUESTION: {question}",
            model=s.router_model,
            max_tokens=120,
        )
        route = out.get("route", "").strip().lower()
        if route in _VALID:
            return cast(Route, route), out.get("reason", "")
    except Exception:
        pass
    # Fail safe: when uncertain, treat as policy so retrieval still runs.
    return "policy", "router fallback"

"""Block Kit builders for Slack messages."""
from __future__ import annotations
import re
from ..types import Trace

# Requires the "#" so other bracketed text (e.g. [REDACTED]) is left alone.
_INLINE_CITATION_RE = re.compile(r"\s*[\[【][a-z0-9_]+#[^\]】]+[\]】]", re.IGNORECASE)


def placeholder_blocks() -> list[dict]:
    return [{
        "type": "section",
        "text": {"type": "mrkdwn", "text": ":mag: _Looking into this..._"},
    }]


def display_text(trace: Trace) -> str:
    """Answer as shown to the user: inline [doc#section] markers removed.

    Sources are deliberately not shown in Slack. Citations stay on the trace
    for the audit log and evals.
    """
    text = _INLINE_CITATION_RE.sub("", trace.answer)
    # Slack mrkdwn bold is *x*, not Markdown's **x**.
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    return re.sub(r"\s+([.,;:!?])", r"\1", text).strip()


def answer_blocks(trace: Trace) -> list[dict]:
    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": display_text(trace)}}
    ]

    # Feedback + escalate buttons
    blocks.append({
        "type": "actions",
        "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "👍"},
             "value": trace.trace_id, "action_id": "fb_up"},
            {"type": "button", "text": {"type": "plain_text", "text": "👎"},
             "value": trace.trace_id, "action_id": "fb_down"},
            {"type": "button", "text": {"type": "plain_text", "text": "Talk to a human"},
             "value": trace.trace_id, "action_id": "escalate"},
        ],
    })
    return blocks

"""Structure-aware chunker for Markdown.

Splits on headings, preserves the breadcrumb (e.g. "3.1 Sick Leave") as the
chunk's `section`. Falls back to fixed-size windows if a section is too long.
"""
from __future__ import annotations
import re
from dataclasses import dataclass


MAX_CHARS = 1200           # ~300 tokens — comfortable for bge-small
OVERLAP_CHARS = 150


@dataclass
class Chunk:
    section: str | None
    content: str


_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def chunk_markdown(text: str) -> list[Chunk]:
    """Split markdown into (section, content) chunks keyed by heading breadcrumbs."""
    lines = text.splitlines()
    sections: list[tuple[list[tuple[int, str]], list[str]]] = []
    stack: list[tuple[int, str]] = []
    current: list[str] = []

    for line in lines:
        m = _HEADING.match(line)
        if m:
            if current:
                sections.append((list(stack), current))
                current = []
            level = len(m.group(1))
            heading = m.group(2).strip()
            stack = [(lvl, h) for lvl, h in stack if lvl < level]
            stack.append((level, heading))
        else:
            current.append(line)
    if current:
        sections.append((list(stack), current))

    chunks: list[Chunk] = []
    for stk, body in sections:
        breadcrumb = " > ".join(h for _, h in stk) if stk else None
        body_text = "\n".join(body).strip()
        if not body_text:
            continue
        for piece in _split_long(body_text):
            chunks.append(Chunk(section=breadcrumb, content=piece))
    return chunks


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    out: list[str] = []
    i = 0
    while i < len(text):
        end = min(i + MAX_CHARS, len(text))
        back = text.rfind(". ", i, end)
        if back != -1 and back > i + MAX_CHARS // 2:
            end = back + 1
        out.append(text[i:end].strip())
        if end >= len(text):
            break
        # Advance by at least 1 char to guarantee progress even when overlap >= window.
        i = max(end - OVERLAP_CHARS, i + 1)
    return [c for c in out if c]

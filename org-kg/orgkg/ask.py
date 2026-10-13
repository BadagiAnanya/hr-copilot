"""Graph-RAG question answering.

Retrieval is done in code (entity linking on the graph + BM25 + neighbourhood expansion), then one
LLM call writes the answer from that context. Agents that prefer to drive retrieval themselves use
the same Graph API through the MCP server instead.
"""
from __future__ import annotations

import os
import re

from .llm import LLM
from .query import Graph

SYSTEM = """You are the organisation's engineering knowledge assistant. You help engineers understand
teams, repositories and code they have never worked with, so they can onboard quickly and arrive at
conversations with other teams already knowing the basics.

Answer only from the provided context, which comes from the organisation's knowledge graph.
- Cite sources inline as [repo] or [repo:path] using only names that appear in the context.
- Lead with the direct answer, then the supporting detail. Prefer short paragraphs and lists.
- When the question is about architecture, describe components and the flow between them.
- If the context does not contain the answer, say what is missing and name the owning team to ask.
- Never invent repositories, files, endpoints or people."""

DEEP = re.compile(r"\b(lld|low.level|implement|how does|how do|flow|code|function|class|method|logic|algorithm|detail)", re.I)
BUDGET = int(os.environ.get("ORGKG_ASK_CONTEXT_CHARS", 28_000))


def retrieve(graph: Graph, question: str) -> tuple[str, list[str]]:
    s, q = graph.s, question.lower()
    blocks: list[tuple[str, str]] = []  # (source label, text)

    def mentioned(name: str) -> bool:
        return len(name) >= 3 and re.search(rf"(?<![\w-]){re.escape(name.lower())}(?![\w-])", q.replace("_", "-").replace(" ", "-") + " " + q) is not None

    teams = [t for t in s.nodes("team") if mentioned(t["name"]) or mentioned(t["id"].split(":", 1)[1])]
    repos = [r for r in s.nodes("repo") if mentioned(r["name"]) or mentioned(r["name"].replace("_", "-"))]
    techs = [t for t in s.nodes("tech") if mentioned(t["name"])]

    directory = "\n".join(f"- {t['team']}: {t['mission']} Repos: {', '.join(t['repos'])}" for t in graph.list_teams())
    blocks.append(("org", f"## Organisation directory\n{directory}"))
    for team in teams:
        blocks.append((team["name"], graph.team_brief(team["name"])))
    hits = graph.search(question, limit=10)
    if not repos:  # no repo named: let the best search hits pick up to two repos to open fully
        for h in hits:
            if h["repo"] and h["repo"] not in [r["name"] for r in repos]:
                repos.append(graph.repo(h["repo"]))
            if len(repos) == 2:
                break
    for repo in repos[:3]:
        name = repo["name"]
        blocks.append((name, graph.repo_doc(name, "overview")))
        blocks.append((name, graph.repo_doc(name, "hld")))
        if DEEP.search(question):
            blocks.append((name, graph.repo_doc(name, "lld")))
        related = graph.related_repos(name, 3)
        if related:
            blocks.append((name, f"Related repos to {name}: " + "; ".join(f"{r['repo']} ({r['team']}; shares {', '.join(r['shared'][:4])})" for r in related)))
    for tech in techs[:2]:
        users = graph.who_uses(tech["name"])
        blocks.append((tech["name"], f"## Who uses {tech['name']}\n" + "\n".join(f"- {u['repo']} (team {u['team']}): {', '.join(u['files'])}" for u in users)))
    for h in hits:
        if h["what"] in ("file", "symbol", "endpoint", "module") and h["path"]:
            label = f"{h['repo']}:{h['path']}"
            if any(lbl == label for lbl, _ in blocks):
                continue
            node_id = f"file:{h['repo']}/{h['path']}"
            notes = s.doc(node_id, "lld") or ""
            source = (s.doc(node_id, "source") or "")[: 2500 if DEEP.search(question) else 1200]
            blocks.append((label, f"## [{label}]\nDesign notes: {notes or '(none)'}\n```\n{source}\n```"))

    context, used, sources = [], 0, []
    for label, text in blocks:
        if used + len(text) > BUDGET:
            text = text[: max(0, BUDGET - used)]
        if not text:
            break
        context.append(text)
        used += len(text)
        if label != "org" and label not in sources:
            sources.append(label)
    return "\n\n---\n\n".join(context), sources


def ask(graph: Graph, llm: LLM, question: str) -> dict:
    context, sources = retrieve(graph, question)
    answer = llm.complete(SYSTEM, f"<context>\n{context}\n</context>\n\nQuestion: {question}")
    return {"answer": answer.strip(), "sources": sources, "context_chars": len(context)}

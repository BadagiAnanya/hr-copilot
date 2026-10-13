"""MCP server: exposes the knowledge graph as tools to any MCP client (Claude Code, Claude Desktop, IDEs, a Slack bot).

    python -m orgkg.mcp_server          # stdio transport

The agent decides which tools to call; all of them are read-only.
"""
from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from .query import Graph, NotFound

mcp = MCPServer("org-knowledge-graph")
_graph: Graph | None = None


def graph() -> Graph:
    global _graph
    if _graph is None:
        _graph = Graph()
    return _graph


def _safe(fn, *args, **kwargs) -> str:
    try:
        result = fn(*args, **kwargs)
    except NotFound as exc:
        return f"Not found: {exc}"
    return result if isinstance(result, str) else json.dumps(result, indent=1)


@mcp.tool()
def list_teams() -> str:
    """List every team in the organisation with its mission and the repositories it owns. Start here when you do not know who owns something."""
    return _safe(graph().list_teams)


@mcp.tool()
def list_repos(team: str = "") -> str:
    """List repositories with a one-line description, maturity, languages and size. Pass a team name to list only that team's repos."""
    return _safe(graph().list_repos, team or None)


@mcp.tool()
def team_brief(team: str) -> str:
    """Briefing on a team: mission, what other teams can rely on it for, how its repos fit together, and what to read before contacting them."""
    return _safe(graph().team_brief, team)


@mcp.tool()
def repo_doc(repo: str, level: str = "overview") -> str:
    """Documentation for a repository. level: 'overview' (what/why/how to run), 'hld' (components, diagram, main flow), 'lld' (per-module design, key symbols, gotchas) or 'readme' (the original README)."""
    return _safe(graph().repo_doc, repo, level)


@mcp.tool()
def repo_structure(repo: str) -> str:
    """Machine-extracted structure of a repository: components and the paths implementing them, HTTP endpoints, technologies, concepts and file list."""
    return _safe(graph().repo_structure, repo)


@mcp.tool()
def explain_path(repo: str, path: str) -> str:
    """Details for one file or directory in a repo: design notes, symbols, what it imports, what imports it, and the source text."""
    return _safe(graph().explain_path, repo, path)


@mcp.tool()
def search(query: str, repo: str = "") -> str:
    """Full-text search across generated documentation, READMEs, source files, symbols and endpoints. Optionally restrict to one repo."""
    return _safe(graph().search, query, repo or None)


@mcp.tool()
def who_uses(technology: str) -> str:
    """Which repositories and teams use a technology (e.g. 'fastapi', 'spring-boot', 'mlflow'), with the files that use it. Use this to find in-house experience."""
    return _safe(graph().who_uses, technology)


@mcp.tool()
def related_repos(repo: str) -> str:
    """Repositories in other parts of the org that share uncommon technologies or concepts with this one."""
    return _safe(graph().related_repos, repo)


if __name__ == "__main__":
    mcp.run()

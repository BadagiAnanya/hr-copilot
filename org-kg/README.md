# org-kg — organisation knowledge graph

Understand any team's code before you talk to them. `org-kg` indexes an organisation's GitHub
repos into a graph (teams → repos → modules → files → symbols, plus technologies, endpoints,
components and concepts), generates an overview, HLD and LLD for every repo, and serves it through
a CLI, a Q&A command, an MCP server and a static wiki.

Architecture, scaling plan and cost model: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

The proof of concept runs on the public repos of one GitHub account, grouped into stand-in teams
in [config/org.toml](config/org.toml).

## Setup

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e ".[groq,dev]"
```

```bash
cp .env.example .env
```

Set `ANTHROPIC_API_KEY` (Claude, the default) or `GROQ_API_KEY` in `.env`. Only `enrich` and `ask`
call an LLM; everything else works offline once the repos are mirrored.

## Build the graph

```bash
.venv/bin/python -m orgkg sync
```

```bash
.venv/bin/python -m orgkg build
```

```bash
.venv/bin/python -m orgkg enrich
```

`sync` mirrors the repos into `data/repos`, `build` extracts structure (no LLM, re-runs only for
repos whose HEAD changed), `enrich` generates the documentation (cached by content hash, so a
re-run only pays for what changed).

## Use it

| I want to… | Command |
|---|---|
| see who owns what | `orgkg teams`, `orgkg repos --team "ML Engineering"` |
| get briefed on a team before contacting them | `orgkg team genai` |
| understand a product | `orgkg repo mini-cdn`, `--level hld`, `--level lld` |
| drill into one file or directory | `orgkg explain mini-cdn ProxyService.java` |
| find code or docs | `orgkg search "cache eviction"` |
| find in-house experience | `orgkg who-uses mlflow`, `orgkg related Network_Security` |
| ask in plain language | `orgkg ask "How does a request flow through mini-cdn?"` |
| browse | `orgkg export` → `out/wiki/index.md`, `out/graph.html` |

(`orgkg` is `.venv/bin/python -m orgkg`, or the `orgkg` script after `pip install -e .`.)

## Use it from an agent (MCP)

The same read API is exposed as MCP tools (`list_teams`, `list_repos`, `team_brief`, `repo_doc`,
`repo_structure`, `explain_path`, `search`, `who_uses`, `related_repos`). For Claude Code, from this
directory:

```bash
claude mcp add org-kg -- "$PWD/.venv/bin/python" -m orgkg.mcp_server
```

## Layout

| Path | Role |
|---|---|
| `orgkg/ingest.py` | GitHub connector: list and mirror repos |
| `orgkg/extract.py` | static analysis → nodes and edges |
| `orgkg/store.py` | SQLite graph + documents + full-text index + LLM cache |
| `orgkg/enrich.py` | LLM overview / HLD / LLD, grounded and cached |
| `orgkg/query.py` | read API used by every consumer |
| `orgkg/ask.py` | graph-RAG question answering |
| `orgkg/mcp_server.py` | MCP tools |
| `orgkg/wiki.py` | markdown wiki and graph visualisation |

## Tests

```bash
.venv/bin/python -m pytest -q
```

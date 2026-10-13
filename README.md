# HR Copilot — Agentic AI on Slack

A production-shaped RAG + tool-use agent for internal HR questions, deployed to Slack.
Built on $0 of infra using free tiers and open-source tools.

**Demo:** [watch the one-minute demo video](docs/alex_demo.webm) — Alex answering HR questions in Slack.

## What it does

A new employee DMs the bot on Slack:
> "How many sick leaves do I have, and can I carry them over to next year?"

The bot:
1. Looks up the user's leave balance via a mock HRMS tool.
2. Answers the carry-forward question from a policy doc, with a citation.
3. If the policy is ambiguous or the question is sensitive, escalates to HR.

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the full diagram.

Request lifecycle:
```
Slack  →  FastAPI (ack <3s)  →  asyncio queue  →  Worker
                                                  │
                      Identity + ACL  ─────────── ▼
                      Router (small LLM)  ────────▼
                      Executor (LangGraph)
                        ├── RAG branch (hybrid retrieval + rerank)
                        └── Tool branch (MCP server → mock HRMS)
                      Generator (big LLM, cited)
                      Guardrails (PII, faithfulness, abstention)
                                                  │
                      Slack chat.update  ◄────────┘
```

## Stack ($0)

| Layer | Choice | Why |
|---|---|---|
| Generator LLM | Llama 3.3 70B via Groq | Fast, free, 90% of Sonnet quality for grounded QA |
| Small LLM | Llama 3.1 8B via Groq | Routing, query rewrite |
| Embeddings | `BAAI/bge-small-en-v1.5` (local) | 384-dim, CPU-friendly |
| Reranker | `BAAI/bge-reranker-base` (local) | Big quality lift, zero cost |
| Vector DB | Postgres + pgvector (HNSW) | ACL joins free |
| BM25 | Postgres `tsvector` | One DB, one query |
| Queue | `asyncio.Queue` in-process | Honest for demo; SQS in prod topology |
| Cache | Redis (local) | L1 embeddings, L2 retrieval, L3 semantic answers |
| Agent | LangGraph | Explicit state → easy to eval |
| Tools | MCP server (stdio) | Framework-agnostic, matches JD |
| Observability | Langfuse (self-hosted) + structured logs | — |
| CI | GitHub Actions | Free for public repos |

## Pragmatic tradeoffs for $0

| Area | Demo build | Production equivalent | Why swapped |
|---|---|---|---|
| Generator | Llama 3.3 70B (Groq free) | Claude Sonnet / GPT-4o | ~4pp faithfulness |
| Queue | asyncio.Queue in-process | AWS SQS | Durability, DLQ |
| BM25 | Postgres tsvector | OpenSearch | Scale, analyzers |
| Hosting | Render free (sleeps) | ECS Fargate | No cold starts |
| Cache | Local Redis | ElastiCache | HA, scaling |

Agent logic, retrieval strategy, eval harness, and MCP tool layer are production-identical.

## Quickstart

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Start Postgres + Redis
docker compose -f docker/docker-compose.yml up -d

# 3. Set your Groq key
cp .env.example .env
# edit .env — add GROQ_API_KEY from https://console.groq.com

# 4. Ingest sample policies
python -m app.ingestion.run

# 5. Run tests
pytest tests/

# 6. Run evals
python -m evals.run

# 7. Start the Slack app
python -m app.main
```

See [`docs/setup.md`](docs/setup.md) for Slack app configuration.

## Eval results

Auto-updated on every push by `.github/workflows/evals.yml`.

| Category | N | Pass% | Faithfulness | Latency p95 |
|---|---|---|---|---|
| Policy | — | — | — | — |
| Tool | — | — | — | — |
| Refusal | — | — | — | — |
| Adversarial | — | — | — | — |

Run `python -m evals.run` locally to populate.

## Repo layout

```
app/
  main.py              — FastAPI + Slack Bolt entrypoint
  config.py            — settings from env
  agent/
    graph.py           — LangGraph state machine
    router.py          — small-LLM intent classifier
    nodes.py           — identity, retrieve, tool, generate, guardrails
    prompts.py         — system prompts (versioned)
  retrieval/
    hybrid.py          — BM25 + dense + ACL filter + rerank
    embeddings.py      — bge-small wrapper
    reranker.py        — bge-reranker wrapper
    store.py           — pgvector + tsvector queries
  ingestion/
    run.py             — Airflow-shaped DAG as plain Python
    chunker.py         — structure-aware splitter
    loader.py          — PDF/MD/txt loaders
  tools/
    mcp_server.py      — MCP server exposing HR tools
    hrms.py            — mock HRMS (SQLite seed)
    tickets.py         — mock ticketing + escalation
  slack/
    bolt_app.py        — slash cmd, mention, DM handlers
    formatting.py      — Block Kit builders
  cache/
    semantic.py        — L3 semantic answer cache
    layers.py          — L1 embedding + L2 retrieval
  observability/
    tracing.py         — Langfuse spans
    audit.py           — append-only audit log
evals/
  run.py               — eval runner
  judges.py            — faithfulness, abstention, citation
  cases/golden.yaml    — 150 labeled test cases
data/
  policies/            — fake HR docs
  seed/employees.sql   — mock employee + leave data
docker/
  docker-compose.yml   — Postgres + Redis + Langfuse
tests/                 — unit tests
.github/workflows/     — CI: tests + evals
docs/
  architecture.md      — the full diagram
  setup.md             — Slack app setup
  tradeoffs.md         — design decisions
```

## Status

Day 1 of 7. See [`docs/plan.md`](docs/plan.md) for the build schedule.

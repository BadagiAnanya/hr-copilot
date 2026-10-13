# Architecture

## Request lifecycle

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            SLACK WORKSPACE                               │
│   Employee DMs bot  │  @mention in channel  │  /ask slash command        │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ HTTPS (events, slash, actions)
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│              INGRESS LAYER  (FastAPI + Slack Bolt)                       │
│  • Verify Slack signature  • Ack <3s  • Post "Looking into this..."     │
│  • Build Job{trace_id, user, channel, ts, text, received_at}            │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ enqueue
                                ▼
                    ┌───────────────────────┐
                    │ asyncio.Queue (demo)  │   → SQS in prod topology
                    └───────────┬───────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                 AGENT WORKERS  (N coroutines)                           │
│                                                                          │
│   Identity+ACL ─▶ L3 cache ─▶ Router ─▶ { policy | tool | compound |    │
│                                           sensitive | out_of_scope }    │
│                                                                          │
│   policy    : retrieve → generate → guardrails                          │
│   tool      : pick_tool → call_tool → generate → guardrails             │
│   compound  : retrieve + tool (parallel) → generate → guardrails        │
│   sensitive : escalation message (no LLM)                               │
│   oos       : polite refusal (no LLM)                                   │
│                                                                          │
│   generate  : CONTEXT + TOOL_RESULTS → answer with [doc#section] cites  │
│   guardrails: PII scrub, abstain if nothing retrieved/called            │
└─────────────────────────────────────────────────┬───────────────────────┘
                                                  │ chat.update
                                                  ▼
                                        ┌──────────────────┐
                                        │  SLACK (user)    │
                                        │  👍 / 👎 /       │
                                        │  "Talk to human" │
                                        └──────────────────┘

── Supporting services ────────────────────────────────────────────────────

┌─────────────── RETRIEVAL ────────────────┐  ┌──────── TOOLS (MCP) ──────┐
│ embed → hybrid SQL (dense + BM25, RRF,   │  │ get_leave_balance         │
│   ACL-filtered) → cross-encoder rerank → │  │ get_employee_profile      │
│   drop below score threshold             │  │ create_hr_ticket          │
│ pgvector HNSW + Postgres tsvector        │  │ Exposed over stdio MCP    │
└──────────────────────────────────────────┘  └───────────────────────────┘

┌─────────────── CACHE (Redis) ────────────┐  ┌──────── OBSERVABILITY ────┐
│ L1 embedding    : sha256(text) → vector  │  │ structlog spans           │
│ L2 retrieval    : (q, acl, filter) → ids │  │ Langfuse (optional)       │
│ L3 semantic ans : nearest Q within ACL,  │  │ audit_log (Postgres)      │
│                   cosine ≥ 0.95, 7d TTL  │  │                           │
└──────────────────────────────────────────┘  └───────────────────────────┘

┌─────────────── INGESTION (python -m app.ingestion.run) ─────────────────┐
│ data/policies/*.md → parse frontmatter → structure-aware chunker →      │
│   bge-small embeddings → upsert chunks (vector + tsvector) → invalidate │
│   semantic cache → quality checks → stats                               │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────── EVAL HARNESS (CI) ───────────────────────────────────────┐
│ evals/cases/golden.yaml → run against agent → string + retrieval +      │
│   tool checks → LLM judges (faithfulness, abstention) → aggregate →     │
│   results/{stamp}.json + latest.md (rendered into README on push)       │
└─────────────────────────────────────────────────────────────────────────┘
```

## Timed lifecycle (demo numbers; validated with evals + load test)

```
t=0ms      User: "how many sick leaves do I get?"
t=50ms     Slack → FastAPI, signature verified
t=120ms    "Looking into this..." posted, 200 returned, job enqueued
t=180ms    Worker picks up job
t=220ms    Identity+ACL resolved
t=320ms    L3 semantic cache lookup → MISS
t=620ms    Router (Llama 3.1 8B) → "policy"
t=760ms    Hybrid retrieval (pgvector + tsvector) → 20 chunks
t=950ms    Reranker → top 5
t=2600ms   Generator (Llama 3.3 70B) → answer + [leave_policy_v3#3.1 Sick Leave]
t=2650ms   Guardrails pass; cache store
t=2720ms   chat.update replaces placeholder
```

Total: ~2.7s. With L3 cache hit: ~400ms.

## Design decisions

| Decision | Why | Tradeoff |
|---|---|---|
| In-process asyncio queue | Zero infra, honest for a demo | No DLQ, no cross-process scaling (prod uses SQS) |
| Postgres for vector + BM25 | One DB, free ACL joins, HNSW is fast enough | Not as scalable as OpenSearch past tens of M chunks |
| RRF over tuned score weights | Hyperparameter-free, robust across query types | Slightly less tunable |
| ACL pre-filter in SQL | Prevents cross-user leaks even on bugs | Must index acl_groups (done — GIN) |
| Cross-encoder reranker | Biggest quality lever for the money | +200ms per query |
| Deterministic tool selector | Easy to eval, deterministic for CI | Need to swap in LLM selector for broader tool catalogs |
| Local embedding + rerank | $0 LLM cost on hot path | ~400ms first-request warmup |
| L3 semantic cache keyed by ACL hash | Prevents cross-tenant leaks | Blunt invalidation on doc change |
| Structured prompts + versioned prompt file | Prompt changes bust the eval cache | Teams must bump VERSION on edits |
```

# Build plan (7 days)

| Day | Theme | Shipped |
|---|---|---|
| **1** | Skeleton + Slack ingress | Project layout, Postgres+Redis via docker, Slack Bolt placeholder flow, asyncio queue, in-memory worker. End of day: bot DMs reply with a stub. |
| **2** | Ingestion + basic RAG | Structure-aware chunker, bge-small embeddings, hybrid SQL retrieval, generator with citations. End of day: bot answers "how many sick leaves do I get?" from the policy doc. |
| **3** | ACL + rerank + router | ACL metadata + GIN index + pre-filter, bge-reranker, LangGraph-shaped router. End of day: router correctly handles sensitive/out-of-scope. |
| **4** | Tools + MCP + handoff | Mock HRMS, deterministic tool selector, MCP stdio server, escalation path. End of day: "how many leaves do I have left" works end-to-end. |
| **5** | Evals | Golden set of 50+ cases, string + retrieval + tool checks, LLM judges for faithfulness/abstention, GitHub Actions. End of day: README has a results table. |
| **6** | Load test + deploy | Mocked-LLM Locust test (worst-case), docker-compose cleanup, deploy to Render. End of day: live link. |
| **7** | README + video | Architecture diagram, tradeoffs doc, "known failures" section, 90-sec Loom demo. End of day: shippable. |

## Cut order (if time slips)

Keep these: evals, ACL filtering, load test, demo video, README.
Cut in this order: Langfuse integration → MCP server (keep tools as Python) →
compound-route parallelism → feedback-button persistence.

## What NOT to add

- Streaming tokens into Slack (hits rate limits fast; use chat.update once).
- A fancy admin UI (nobody reviewing a portfolio will log in).
- Full auth / OAuth / multi-tenancy (one Slack workspace is fine for the demo).
- A second provider or local Llama fallback (one provider works; mention it).

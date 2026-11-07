# Backlog

Ticket files live in `docs/tickets/T-<nnn>.md`. Status: draft | approved | in-progress | in-review | changes-requested | done.
Only the human (via Gate A) moves a ticket from draft to approved.

## Phases

| Phase | Name | Tickets | Status |
| ----- | ---- | ------- | ------ |
| P0 | Foundation, bug fixes, 5 endpoints, llm wrapper | T-001..T-008 | done |
| P1 | Retrieval quality: chunking, hybrid, rerank, citations, summarization | T-009..T-012 | approved |
| P2 | Read-only tools | T-013..T-015 | approved |
| P3 | LangGraph agent, Ollama, SSE | T-016..T-019 | approved |
| P4 | Sessions, injection defences, limits, golden-set evals | T-020..T-023 | approved |
| P5 | Service platform: pgvector, object store, auth, tenancy, doc-intelligence, deploy (all before frontend) | T-024..T-033 | approved |

## Tickets

Parallel after T-001: T-002, T-003, T-005. After those: T-004 and T-006 chain; T-012 and T-010 independent of each other; T-018 independent anytime after T-002; T-014 and T-015 parallel; T-021 and T-022 parallel after T-019.

| Ticket | Phase | Title | Size | Depends on | Status |
| ------ | ----- | ----- | ---- | ---------- | ------ |
| T-001 | P0 | uv/pyproject, module layout, env, prompts, overview doc | M | - | done |
| T-002 | P0 | Config, logging, llm wrapper with mock mode | M | T-001 | done |
| T-003 | P0 | Fix prototype bugs | S | T-001 | done |
| T-004 | P0 | Model registry in lifespan + threadpool | M | T-002, T-003 | done |
| T-005 | P0 | SQLite document registry | S | T-001 | done |
| T-006 | P0 | Ingestion + embedded Chroma with doc_id metadata | M | T-004, T-005 | done |
| T-007 | P0 | Five endpoints (needs_architect) | M | T-002, T-006 | done |
| T-008 | P0 | Upload validation hardening | S | T-007 | done |
| T-009 | P1 | Parent-child chunking | M | T-006 | done |
| T-010 | P1 | Hybrid BM25 + dense RRF | M | T-009 | done |
| T-011 | P1 | Rerank, abstain, citations | M | T-004, T-010 | done |
| T-012 | P1 | Map-reduce summarization + compression | M | T-007, T-009 | done |
| T-013 | P2 | Tool framework + search/list/get_page tools | M | T-011 | done |
| T-014 | P2 | summarize_document, compare_documents tools | S | T-012, T-013 | done |
| T-015 | P2 | query_table tool | M | T-013 | done |
| T-016 | P3 | Agent graph core + bounds | M | T-014, T-015 | done |
| T-017 | P3 | Grade, rewrite, groundedness | M | T-016 | done |
| T-018 | P3 | Ollama provider | S | T-002 | done |
| T-019 | P3 | Agent SSE endpoint (needs_architect) | M | T-017 | done |
| T-020 | P4 | Per-session memory (needs_architect) | M | T-019 | done |
| T-021 | P4 | Prompt-injection defences | M | T-019 | done |
| T-022 | P4 | Rate limit, spend cap, PII-safe logs | M | T-019 | done |
| T-023 | P4 | Golden set + eval script (eval: evaluator) | M | T-020, T-021, T-022 | done |
| T-024 | P5 | ADR-0003: Postgres+pgvector, VectorStore/ObjectStore, auth and tenancy model (needs_architect) | S | - | approved |
| T-025 | P5 | VectorStore interface + pgvector impl + docker compose dev DB | M | T-024 | approved |
| T-026 | P5 | Move registry, sessions, BM25 metadata to Postgres; retire Chroma | M | T-025 | approved |
| T-027 | P5 | ObjectStore (S3/R2 + local), persist originals, wire query_table table_loader | M | T-026 | approved |
| T-028 | P5 | Service auth: user tokens + tenant API keys (needs_architect) | M | T-024 | approved |
| T-029 | P5 | Multi-tenancy: tenant_id everywhere + per-tenant daily spend cap | M | T-026, T-028 | approved |
| T-030 | P5 | Doc-intelligence endpoints: summary + sentiment with citations (eval: evaluator) | M | T-029 | approved |
| T-031 | P5 | Cross-document comparison endpoint + evals (eval: evaluator) | M | T-030 | approved |
| T-032 | P5 | /v1 versioning, OpenAPI spec, /health, CORS from env, docs off in prod | S | T-029 | approved |
| T-033 | P5 | Dockerfile + staging(dev)/prod(main) deploy docs; deployer staging smoke | M | T-027, T-031, T-032 | approved |

Estimated total: 18 M x $4 + 5 S x $1.50 = $79.50 (caps, not expected spend).

## Later (not built)

- Frontend (BLOCKED until all of P5 / T-033 is done)
- Dev/prod environments and containers (now P5: T-033)
- Performance, safety and security testing (load tests, p95 measurement, pen tests)
- Complex-document handling (OCR, scanned PDFs, complex tables)
- Full-architecture evals phase
- Real auth replacing the `X-User-Id` header (now P5: T-028)
- Git history rewrite for personal documents (human task)
- Rotate the exposed `.env` OpenAI key (human task)
- Reverse-proxy request body-size limit (Starlette spools multipart bodies before our 10 MB streaming cap runs); part of the environments/deploy phase.
- PDF safety checks (encrypted, embedded JavaScript/files) using pypdf, if wanted; the unused PyPDF2/VirusTotal prototype is deleted in T-004.
- Streaming request-body guard (ASGI middleware) so the 50 MB total and 10-file limits are enforced before Starlette spools the multipart body, including chunked requests (reviewer note on T-007; same root cause as the proxy body-size item).
- Move GET /v1/documents pagination into SQL LIMIT/OFFSET instead of slicing in memory.
- Backfill BM25 indexes for documents ingested before T-010 (they stay dense-only until re-uploaded); document `MAX_UPLOAD_MB` (lowers the 10 MB cap, never raises it) in `.env.example` (human).
- Vector store `query` counts matches via `get(include=[])`; swap for a cheaper count. BM25-only hits carry placeholder distance 1.0 and per-doc idf is not cross-doc comparable: revisit in T-011 abstention.
- Calibrate MIN_RERANK_SCORE on the real bge reranker with the evaluator (0.0 is a placeholder; add the var to `.env.example`, human). Optional T-012 nits: escape double quotes in document names inside prompts; do not return a 200 partial on non-budget LLM errors after the first map call.
- T-013 nits: _search lists owner docs twice; wrap() truncates after escaping (cosmetic partial entity); note in tools.md that callers must pass settings.min_rerank_score into ToolContext.
- REQUIRED before the agent endpoint (T-019) is useful: original csv/xlsx uploads are not persisted, so query_table has no table_loader and returns not found in production. Persist originals (owner-scoped, size-capped, cleaned on document delete) and wire ToolContext.table_loader; needs a ticket (and likely an ADR note).
- Agent loop (T-016) must count tool LLM calls (summarize_document, compare_documents) against the per-run USD/token budget and pass settings.min_rerank_score into ToolContext; keep module state used by _run_async free of loop-bound objects.
- query_table: no wall-clock timeout and pandas work is synchronous; consider asyncio.to_thread plus a timeout.
- AGENT_MAX_TOKENS env var (currently constant 30000 in services/agent/graph.py); human adds it to .env.example (T-016 review).
- Move agent stream citations to chunk-level `Citation` (page/sheet/span) before a frontend depends on the doc-level `{doc_id, filename, span}` shape (T-019 review).
- T-020 (sessions): validate `session_id` before the stream opens, add a history param to `run_agent`, echo `session_id` in `final`.
- T-022 (limits): rate-limit and spend-cap checks before the stream opens; record per-user spend from `final.usage`, including partial spend on disconnect.
- T-021 re-review approved (round 2). Nits: link-opener floods (![]( repeated) cost up to ~120 ms per 20k call, lower the _IMAGE/_LINK target cap or short-circuit when no ) exists; tools.wrap can leave a harmless partial entity after truncation; homoglyph role words and plain-language social engineering remain known gaps.
- T-022 follow-ups: per-IP rate limit (needs a trusted proxy); upload rate limit and stored-documents cap per owner; shared (Redis) limiter state for multi-worker.
- Security audit follow-ups (docs/security-audit.md): B-1 real auth; H-1 upload limits/quotas; H-2 streaming body-size middleware; H-3 zip/parse hardening and timeouts; H-4 CORS/security headers; H-5 disable docs/trim healthz in prod; M-3 fail closed on unknown model price; L-1 tracemalloc; L-2 delete load_xls; L-4 periodic session purge; L-5 pin HF model revisions.

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
| T-015 | P2 | query_table tool | M | T-013 | approved |
| T-016 | P3 | Agent graph core + bounds | M | T-014, T-015 | approved |
| T-017 | P3 | Grade, rewrite, groundedness | M | T-016 | approved |
| T-018 | P3 | Ollama provider | S | T-002 | done |
| T-019 | P3 | Agent SSE endpoint (needs_architect) | M | T-017 | approved |
| T-020 | P4 | Per-session memory (needs_architect) | M | T-019 | approved |
| T-021 | P4 | Prompt-injection defences | M | T-019 | approved |
| T-022 | P4 | Rate limit, spend cap, PII-safe logs | M | T-019 | approved |
| T-023 | P4 | Golden set + eval script (eval: evaluator) | M | T-020, T-021, T-022 | approved |

Estimated total: 18 M x $4 + 5 S x $1.50 = $79.50 (caps, not expected spend).

## Later (not built)

- Frontend
- Dev/prod environments and containers
- Performance, safety and security testing (load tests, p95 measurement, pen tests)
- Complex-document handling (OCR, scanned PDFs, complex tables)
- Full-architecture evals phase
- Real auth replacing the `X-User-Id` header
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

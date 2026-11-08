# DocChat RAG spec

Scope of this spec: phases P0-P4 (backend only). Frontend, environments, hardening tests, complex documents and full evals are in `docs/backlog.md` under Later.

## Goal
Turn the prototype in `src/backend/app` into a production-shaped agentic document-RAG backend. Users upload one or many documents, then summarize them, ask questions with citations, compare them, and query tables, through an agent that plans, calls read-only tools, grades its own retrieval, retries, and checks that the answer is grounded.

## Non-goals
- Frontend, deployment environments, load/security test suites, OCR/scanned PDFs, complex table extraction, full-system evals (all Later).
- Multi-tenant billing, SSO, fine-tuning, writing or modifying user documents, any tool with side effects.

## Users and flows
Single developer/user now; the API is built per-user (a `X-User-Id` header stands in for real auth until the Later phase).
1. Upload files (`POST /documents`) and receive `doc_id`s. 2. List or delete them. 3. `POST /summarize` for a whole-document summary. 4. `POST /query` asks a question (P1: single-shot RAG; P3: agent, streamed over SSE). 5. Follow-ups reuse a `session_id` (P4).

## Features (v1)
- P0: bug fixes, models loaded once, non-blocking, 5 endpoints, per-document ids and metadata, `llm` wrapper with mock mode, pydantic-settings config, uv with pinned deps, `prompts/*.md`.
- P1: hybrid BM25 + dense retrieval, cross-encoder rerank, parent-child chunks with page/section metadata, citations (source id + span), "I don't know" on weak retrieval, optional LLM compression, map-reduce summarization.
- P2: tools `search_documents`, `summarize_document`, `get_page_or_section`, `list_documents`, `query_table`, `compare_documents`; allow-listed, read-only, scoped to the caller's `doc_id`s.
- P3: LangGraph agent loop (router, plan, tool, grade, rewrite/retry, answer with citations, groundedness check); bounded by steps, tokens and USD; SSE step streaming; configurable provider.
- P4: per-session memory, prompt-injection defences, per-user rate limit and daily spend cap, PII-safe logging, ~20-item golden-set eval script.

## Stack
Python 3.12, uv (pinned lockfile), FastAPI + Pydantic v2, pydantic-settings, Ruff, pytest + httpx. Chroma (embedded persistent client, no separate server), `sentence-transformers` (MiniLM embeddings, `bge-reranker-base`), `rank-bm25`, pandas, LangGraph. Hosted Claude via the `anthropic` SDK for the agent; Ollama optional. Exact dependency list is decided in the ADR; none is added without it.

## Architecture
Layers: `api/` (routers, SSE) -> `services/` (ingestion, retrieval, summarization, agent) -> `core/` (config, llm wrapper, model registry, logging) -> `store/` (Chroma, doc registry, BM25 index). Models are loaded once in the FastAPI lifespan and held in a registry; CPU/blocking work runs in a threadpool. Agent tools call services, never the store directly. One doc per component plus an overall doc in `docs/architecture/`, written in the PR that introduces the component.

## Data model
- `Document`: `doc_id`, `owner_id`, `filename` (original), `mime`, `size`, `sha256`, `pages_or_sheets`, `created_at`, `status`.
- `Chunk` (child, embedded) -> `parent_id` (larger context span): `chunk_id`, `doc_id`, `page|sheet`, `section`, `char_start`, `char_end`, `text`.
- `Citation`: `doc_id`, `filename`, `chunk_id`, `page|sheet`, `span`.
- `Session` (P4): `session_id`, `owner_id`, bounded message history.
Registry is SQLite via the stdlib (no new server); Chroma holds vectors keyed by `doc_id` metadata. Re-uploading identical content (`sha256`) returns the existing `doc_id`.

## AI design (models, prompts, retrieval/agent loop, guardrails, cost per request)
Recorded line by line in `docs/ai-design.md` (rationale per row). Summary: embeddings `all-MiniLM-L6-v2` (local, free); rerank `bge-reranker-base` (local); generation default `claude-sonnet-5-5` for the agent and `claude-haiku-5-5` for map/grade/compress steps, model ids in config only; Ollama as the optional local provider. Prompts in `prompts/*.md`, versioned. Retrieved text and tool output are data, never instructions. Answers must cite or say "I don't know" when the top rerank score is under a threshold. Target cost: at most $0.05 per agent request, $0.02 per summarize call on a typical 20-page document.

## Evals and targets (metric, dataset, threshold)
Golden set `evals/golden.jsonl` (~20 items, P4) over small fixture documents in `evals/fixtures/`. Thresholds live in `evals/thresholds.json` (already set): recall@5 >= 0.8, MRR >= 0.6, faithfulness >= 0.85, relevance >= 0.8, citation_correct >= 0.9, agent task_success >= 0.8 with avg steps <= 8, injection block rate >= 0.9. Unit/integration tests use the mock LLM and run free. The full-architecture eval phase is Later.

## Security and abuse
- Upload: size cap 10 MB (streamed, rejected before full read), extension allowlist, content sniff that must agree with the extension, filename sanitized, temp files always removed, no code execution from documents.
- Agent: allow-listed read-only tools, every tool call scoped to the caller's `doc_id`s, bounded steps/tokens/USD per run, tool results treated as untrusted and wrapped in delimiters.
- Per-user rate limit and daily spend cap on every LLM endpoint. Logs never contain document text or prompts with PII; only ids, sizes, timings, tokens and USD.
- Secrets only from env, documented in `.env.example`. Real key rotation for the committed `.env` is a human task (see Risks).

## Performance targets
p95 latency <= 8 s for `/query` on a 20-page document with the hosted model; ingestion of a 20-page PDF <= 10 s on a laptop CPU; cold start loads models once (target <= 30 s); event loop never blocked by parsing or inference. Measured properly in the Later perf phase.

## Deploy target and env vars
Local first: `uv run uvicorn`, embedded Chroma at `CHROMA_DIR`, SQLite at `DB_PATH`. Containers and dev/prod environments are Later. Env vars (all in `.env.example`): `ANTHROPIC_API_KEY`, `LLM_PROVIDER` (`anthropic|ollama|mock`), `AGENT_MODEL`, `FAST_MODEL`, `OLLAMA_BASE_URL`, `CHROMA_DIR`, `DB_PATH`, `MAX_UPLOAD_MB`, `RATE_LIMIT_PER_MIN`, `DAILY_USD_CAP`, `AGENT_MAX_STEPS`, `AGENT_MAX_USD`.

## Risks
- Committed `.env` contains an `OPEN_AI_API_KEY` and was read by tools earlier; treat as exposed and rotate (human).
- Personal documents (citizenship tracker) exist in git history; decide whether to rewrite history (human, not done by agents).
- Mistral-7B-class local models are unreliable at tool calling, so Ollama is best-effort for the agent.
- Local cross-encoder plus embeddings add memory (~2 GB) and cold-start time; mitigated by loading once.
- Hosted-model spend: bounded by per-run and per-day caps; tests never call paid APIs.

## Assumed defaults (not asked)
Hosted Claude for the agent, Haiku-class for sub-steps; embedded Chroma replaces the HTTP Chroma server; SQLite registry; header-based user id until real auth; 10 MB upload cap kept; no OCR; Python 3.12 and uv per the stack overlay; single-process deployment.

## P5 Service platform (pre-frontend)
Goal: run the backend as ONE standalone containerised service (Fly/Railway/Render, not Vercel serverless) that serves (a) a standalone DocChat web app (Vercel frontend, later) and (b) an external app's backend via service-to-service tenant API key. All of P5 completes before any frontend ticket starts.
- Vector store: Postgres + pgvector behind a `VectorStore` interface (Chroma retired). Postgres also holds the document registry, sessions and metadata. New dependencies need ADR-0003 first. Local dev via docker compose; tests use a fake/in-memory VectorStore and never need a server.
- Object storage: `ObjectStore` interface (S3/R2-compatible impl + local-fs impl for dev/tests) for uploaded originals; persists originals so `query_table` works (closes the table_loader gap).
- Auth: end-user bearer tokens and tenant API keys (hashed at rest, rotatable, revocable) replace the `X-User-Id` header (audit B-1).
- Multi-tenancy: `tenant_id` on documents, chunks/vectors, sessions and queries; every read/write scoped by tenant then owner; per-tenant daily USD cap on top of per-user caps.
- Document intelligence endpoints under `/v1`: per-document summary, sentiment, cross-document comparison. Each returns citations, uses prompts in `prompts/*.md`, has mock-mode tests and evals with thresholds.
- API surface: `/v1` versioned, published OpenAPI spec (`docs/openapi.json`, generated and checked in CI), `/health`, CORS allow-list from env, docs disabled in prod.
- Deploy: Dockerfile, `dev` -> staging service, `main` -> production service. Deployer prepares/verifies staging only; promotion stays Gate B (human).
New env vars (documented in `.env.example` by the human): `DATABASE_URL`, `OBJECT_STORE` (`s3|local`), `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `AUTH_TOKEN_SECRET`, `TENANT_DAILY_USD_CAP`, `CORS_ALLOW_ORIGINS`, `ENABLE_DOCS`.

## P6 Frontend (parallel with P5; human override of "frontend after T-033")
Goal: a portfolio-grade standalone web app (Vercel, static) for DocChat: upload documents, chat with streamed agent steps and citations, manage sessions. Talks only to the `/v1` API on the `dev`/staging backend.
- Users/auth: NO login or accounts (portfolio). Each visitor gets an anonymous short-lived bearer user token from `POST /v1/auth/anonymous` (new P5 ticket, depends on T-028), kept in `sessionStorage`, sent as `Authorization: Bearer`. 401 -> mint a new token silently and retry once. Tenant API keys never reach the browser.
- Abuse/cost controls (backend, in the new P5 ticket): per-IP mint rate limit, existing per-user + global + tenant daily USD caps, document TTL cleanup (default 24h), "demo data resets" notice in the UI.
- Stack: Vite + React + TypeScript SPA, static output, no server functions. Exact libs and versions in ADR-0004 (architect). Typed client generated from `docs/openapi.json` (hand-typed contract until T-032 publishes it). SSE via `fetch` streaming (EventSource cannot send headers).
- Mock mode: `VITE_MOCK=1` runs the app against an in-browser mock API (MSW) with canned SSE; used for dev, tests, E2E and as a static portfolio demo when the backend is down.
- Pages: Documents (upload up to 10 files, list, delete, status, per-file errors), Chat (streamed step timeline, token stream, citations, abstain/stop reasons, session list and switch), Summary/Sentiment/Compare views (after T-030/T-031 land; behind a feature flag until then).
- Security: all model/retrieved text rendered as text (no `dangerouslySetInnerHTML`, no raw HTML/markdown HTML); backend already HTML-escapes stream text, frontend must not double-decode into HTML; strict CSP header via `vercel.json`; no secrets or provider keys in the bundle; `VITE_API_BASE_URL` is the only env; links from model text are not auto-followed.
- Tests: Vitest + Testing Library (unit/component), Playwright E2E in mock mode. Verify command (human adds to `.claude/kit.json`): `cd web && npm run lint && npm run typecheck && npm test -- --run`; e2e: `cd web && npx playwright test`.
- Deploy: Vercel project rooted at `web/`, preview from `dev`; production promotion is Gate B (human).
- Contract risks with in-flight P5: (1) no anonymous-mint endpoint yet (new ticket); (2) T-028 rewrites auth: client must isolate it in one module; (3) T-032 `/v1` + OpenAPI + CORS allow-list: need `CORS_ALLOW_ORIGINS` to include the Vercel origin and allow `Authorization` header and SSE; (4) stream citations are doc-level `{doc_id, filename, span:str}` vs non-stream chunk-level `Citation` (backlog item): UI types must tolerate both until fixed; (5) T-029 tenancy changes 404 semantics only, no shape change expected; (6) T-030/T-031 response shapes unknown: frontend tickets for those depend on them; (7) T-026 moves sessions to Postgres: session list endpoint may not exist yet (verify; add to P5 if missing); (8) error body `{detail, code}` and 429 codes `rate_limited`/`spend_cap_exceeded`/`budget_exceeded` must map to UI messages.
- Non-goals: login/signup, password reset, teams, billing, i18n, mobile app.

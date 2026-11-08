# Status (rolling, max 60 lines; orchestrator rewrites after every stage)

Updated: 2026-11-07 · Mode: autopilot · Phase: P5 DONE (T-024..T-033) on dev @1070aa1 · P5 audit done (docs/security-audit-p5.md) · T-034..T-037 DONE (frontend enablers) · AT GATE B (human)

## Decisions made
- Spec (docs/spec.md): P0-P4 backend only. Later items in docs/backlog.md.
- ADR-0001/0002 still "proposed": review at Gate B. Integration branch `dev`; tickets squash-merged directly, NO PRs (CLAUDE.md override).
- No Anthropic key; build and all tests ran on LLM_PROVIDER=mock. No paid API was called by agents.
- Use `node --experimental-strip-types scripts/agentic/*.ts` (plain `node` fails on Node 22).
- MAX_REWRITES default is 1 (was 2): out-of-corpus questions burned ~11 steps for no gain.

## Phase P5 DONE (T-024..T-033, all merged to dev, 1057 tests)
- Shipped: ADR-0003; VectorStore (pgvector + in-memory), Chroma retired; Postgres registry/sessions + migrations (human-run, local-only guard); ObjectStore (local/S3) + query_table loader; HMAC user tokens + hashed tenant API keys (X-User-Id gone); tenant_id everywhere + per-tenant daily cap; /v1/documents/{id}/summary|sentiment, /v1/compare; app factory, /health, env CORS, ENABLE_DOCS, docs/openapi.json (+drift test); Dockerfile + docs/deploy.md.
- Audit P5: 0 blocker, 3 high (upload quota, Content-Length trust, parse limits), 5 med (M1 unknown model priced 0 bypasses caps, M4 no persistent API-key store/issuance), 9 low. OK for staging with invited tenants, 1 replica; NOT open internet.
- Frontend enablers DONE on dev @8f5149e (1104 tests): T-034 POST /v1/auth/anonymous (per-IP /64-aware limit, demo tenant caps, token_expired vs invalid_token); T-035 GET /v1/sessions (+ escaped message content on read); T-036 anonymous TTL purge (loop/cron, default off); T-037 chunk-level stream citations (span is now an object: breaking). Local release/prod = dev, NOT pushed.
- New .env.example vars (human): ANON_TOKEN_TTL_SECONDS=3600, ANON_MINT_RATE_PER_HOUR=10, ANON_USER_DAILY_USD_CAP=0.10, ANON_TENANT_DAILY_USD_CAP=1.0, DEMO_TENANT_ID=demo, TRUSTED_PROXY=, ANON_DATA_TTL_HOURS=24, PURGE_INTERVAL_SECONDS=0, PURGE_ON_START=false, PURGE_BATCH_SIZE=200.
- Open items: staging smoke not run (no staging service; human creates it); image 10.2 GB (CUDA torch, CPU-only torch needs ADR); model revisions unpinned; parents store SQLite + BM25 per-process; live eval still needed (mock misses recall@5/citation_correct/task_success, unchanged).
- Local release/prod = dev (ff, NOT pushed, 67 ahead of origin). User chose local-only; push and dev->main PR are human.
- Human: .env.example vars (DATABASE_URL, POSTGRES_PASSWORD, DB_POOL_MAX, AUTH_TOKEN_SECRET, TENANT_DAILY_USD_CAP, OBJECT_STORE, OBJECT_STORE_PATH, S3_*, CORS_ALLOW_ORIGINS, ENABLE_DOCS; drop CHROMA_DIR); CI drift step; delete untracked data/objects/, gitignore data/.

## Progress
- Done on dev (944 tests, ruff clean): T-001..T-023. Last commit 6ac2220.
- Evals (mock only, labelled so): recall@5 0.786 (<0.8), citation_correct 0.765 (<0.9), task_success 0.75 (<0.8) MISS; MRR, faithfulness, relevance, steps 7.75, injection 1.0 pass. `uv run python -m evals.run --mock --check` exits 1. Causes traced to the lexical FAKE reranker / toy answerer; needs a live eval. Do not add the CI step until resolved or waived.
- Security audit: docs/security-audit.md. Fine for local/demo; NOT for internet exposure (B-1 X-User-Id trust-the-header auth, H-1..H-5).

## Open (human)
- GATE B: review/merge dev -> main (see final report for the command). Never merged by agents.
- .env.example (agents cannot touch it): MIN_RERANK_SCORE=0.0, MAX_UPLOAD_MB, SUMMARY_MAX_USD, SUMMARY_REDUCE_ROLE, SUMMARY_COMPRESS, SUMMARY_BATCH_CHARS, SUMMARY_MAX_BATCHES, SESSION_MAX_MESSAGES=20, SESSION_MAX_TOKENS=2000, SESSION_TTL_HOURS=24, RATE_LIMIT_PER_MIN=30, DAILY_USD_CAP=1.0 (must be >0), GLOBAL_DAILY_USD_CAP=5.0, GLOBAL_RATE_LIMIT_PER_MIN=120, SESSION_MAX_PER_OWNER=100; check AGENT_MAX_STEPS (code default 16; a stale 6 would override); optional AGENT_MAX_TOKENS (code constant 30000).
- Before production: real auth, upload quotas/body limits, parse sandbox/timeouts, CORS+security headers, docs off, pip-audit (audit file).
- query_table is unusable in production: originals are not persisted, table_loader unwired (needs a ticket).
- .gitignore (human): add data/, chroma_db*/, *.db. Rotate the exposed OpenAI key.
- Personal documents still tracked in git and history: src/documents/ (hotel reservation PDF, travel checklist). Rewrite is a human decision.
- Live LLM later: ANTHROPIC_API_KEY or Ollama; calibrate MIN_RERANK_SCORE and grader accuracy on real models.

## Phase P6 Frontend (GATE A, waiting on human) - 2026-11-07
- Human override: frontend built in parallel with P5. No login (portfolio): anonymous per-visitor bearer token via POST /v1/auth/anonymous (T-034), sessionStorage. ADR-0004 (proposed): Vite+React+TS SPA, MSW mock mode, hand-rolled SSE.
- Tickets T-034..T-037 (P5 enablers) and T-038..T-047 (P6, web/) are `draft`. T-038..T-045 are mock-first and need no P5 work; T-046 blocked on T-030/T-031; T-047 (real API switch) needs T-032/T-034..T-037.
- T-032 (already approved) got 3 extra acceptance lines (CORS/SSE/OpenAPI): re-approve at Gate A.
- Human-only: kit.json web verify, .env.example vars (see T-034/T-036/T-038/T-046), CI jobs, Vercel project, ADR-0004 accept.

## P6 update 2026-11-08
- T-034..T-037 done. Design approved: docs/design/ (prototype.html). Tickets T-038..T-047 + new backend T-048 (cited passage endpoint) set to `approved` on the human's go-ahead. T-046 unblocked (T-030..T-032 done). Types generated from docs/openapi.json; span is an object.
- HUMAN BEFORE /autopilot: add web verify to .claude/kit.json (else frontend tickets are not verified); add VITE_API_BASE_URL, VITE_MOCK to .env.example.

## Autopilot P6 progress 2026-11-08
- Done on dev: T-038 (web scaffold, cbbdc21), T-048 (chunk passage endpoint, ee3e349). Next: T-039 -> (T-040, T-041) -> T-042 -> T-043 -> T-044/T-045 -> T-046 -> T-047.
- Backlog adds: rate-limit cheap read routes; source-pane highlight may be truncated (>4000 chars).
- Human still: .env.example VITE_* vars, ADR-0004 accept, CI jobs, Vercel project.

## P6 DONE 2026-11-08 - AT GATE B (human)
- All P6 + enabler tickets done on dev: T-034..T-048 (15). Verify green: ruff, 1121 pytest, web 337 unit + 26 Playwright E2E (CSP enforced), build, npm audit 0 vulns.
- Security audit: docs/security-audit-web.md. 0 blockers; public demo YES with 5 conditions; H1 (anon upload quota) should be fixed before exposing a URL.
- Human before deploy: kit.json already updated; .env.example VITE_API_BASE_URL, VITE_MOCK, VITE_FEATURE_DOC_INTEL (web/.env.example + root); regenerate web/vercel.json with real API_ORIGIN; Vercel project (root web/); backend CORS_ALLOW_ORIGINS; CI jobs (.github); accept ADR-0004; PURGE_INTERVAL_SECONDS; local ingest RuntimeError to diagnose.

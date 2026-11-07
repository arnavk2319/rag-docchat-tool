# Status (rolling, max 60 lines; orchestrator rewrites after every stage)

Updated: 2026-11-07 · Mode: autopilot · Phase: ALL 23 TICKETS DONE on dev · security audit done · AT GATE B (human)

## Decisions made
- Spec (docs/spec.md): P0-P4 backend only. Later items in docs/backlog.md.
- ADR-0001/0002 still "proposed": review at Gate B. Integration branch `dev`; tickets squash-merged directly, NO PRs (CLAUDE.md override).
- No Anthropic key; build and all tests ran on LLM_PROVIDER=mock. No paid API was called by agents.
- Use `node --experimental-strip-types scripts/agentic/*.ts` (plain `node` fails on Node 22).
- MAX_REWRITES default is 1 (was 2): out-of-corpus questions burned ~11 steps for no gain.

## Phase P5 (PLAN GATE, waiting on human)
- P5 Service platform drafted: T-024..T-033 (all `approved` by human (Gate A)), spec section "P5 Service platform (pre-frontend)". Human decisions: one standalone container service; Postgres+pgvector behind VectorStore (ADR-0003 in T-024); S3/R2 ObjectStore; user tokens + tenant API keys; tenant_id everywhere; summary/sentiment/compare endpoints; /v1 + OpenAPI; Dockerfile + staging deploy.
- Order: T-024 -> (T-025 -> T-026 -> T-027) and (T-028) -> T-029 -> (T-030 -> T-031) and T-032 -> T-033. Frontend is BLOCKED until T-033 is done.
- Eval metric names/thresholds in T-030/T-031 are planner proposals: evaluator to align with evals/thresholds.json. New env vars listed in spec P5 go in .env.example (human).

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

# Status (rolling, max 60 lines; orchestrator rewrites after every stage)

Updated: 2026-11-07 · Mode: autopilot · Phase: P0 (foundation) finishing, P1 starting

## Decisions made
- Spec (docs/spec.md): P0-P4 backend only. Later items in docs/backlog.md.
- ADR-0001 (proposed): FastAPI + SQLite + embedded Chroma + rank-bm25, local MiniLM + bge rerank, hosted Claude via one `llm` wrapper (Ollama optional), LangGraph agent, local-first.
- ADR-0002 (proposed): API contracts (/v1, X-User-Id dependency, sync ingestion with per-file UploadResult, failure_reason column, SSE events, explicit sessions).
- 23 tickets, all approved by the human (cap $79.50). Integration branch `dev`; tickets squash-merge directly, NO PRs (CLAUDE.md override).
- No Anthropic key; build and all tests run on LLM_PROVIDER=mock. No paid API is called by agents.

## Progress
- Done on dev (443 tests): T-001 uv layout, T-002 config + llm wrapper, T-003 upload hardening, T-004 model registry + parsers, T-005 SQLite registry, T-006 ingestion + embedded Chroma, T-009 parent-child chunking, T-018 Ollama provider.
- In flight: T-007 five endpoints (merging dev, then tests and review).
- Next: T-008 upload hardening, then P1 (T-010 hybrid, T-011 rerank/citations, T-012 map-reduce) after the budget resets.

## Budget
- BUDGET WARN: day spend $25.55 of $30.00 (85%) on 2026-11-07 (week $25.55 of $90). Plan: finish T-007 and T-008 (closes P0), then pause at the ticket boundary; resume P1 (T-010+) tomorrow or after the human raises `day_usd` in .claude/budgets.json (human-only). About $16.50 of today's spend is orchestrator overhead from the long session.

## Open (human)
- Add to .env.example (agents cannot touch it): EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2, RERANK_MODEL=BAAI/bge-reranker-base, MODELS_FAKE= (test-only, leave empty), OLLAMA_AGENT_MODEL=llama3.1:8b, OLLAMA_FAST_MODEL=llama3.2:3b.
- Live LLM later: ANTHROPIC_API_KEY in .env (or Ollama). T-023 evals run on scripted mock answers until then.
- Git history still holds the personal citizenship documents (Later).
- ADR-0001/0002 are still "proposed": review at Gate B.

# Status (rolling, max 60 lines; orchestrator rewrites after every stage)

Updated: 2026-11-07 · Mode: autopilot · Phase: P0 complete (9/9); P1 in progress (T-009, T-010 done)

## Decisions made
- Spec (docs/spec.md): P0-P4 backend only. Later items in docs/backlog.md.
- ADR-0001 (proposed): FastAPI + SQLite + embedded Chroma + rank-bm25, local MiniLM + bge rerank, hosted Claude via one `llm` wrapper (Ollama optional), LangGraph agent, local-first.
- ADR-0002 (proposed): API contracts (/v1, X-User-Id dependency, sync ingestion with per-file UploadResult, failure_reason column, SSE events, explicit sessions).
- 23 tickets, all approved by the human (cap $79.50). Integration branch `dev`; tickets squash-merge directly, NO PRs (CLAUDE.md override).
- No Anthropic key; build and all tests run on LLM_PROVIDER=mock. No paid API is called by agents.

## Progress
- Done on dev (546 tests): T-001..T-010 and T-018. Latest: T-008 upload hardening (413/415, xls dropped, ADR-0002 amended), T-010 hybrid BM25+dense RRF.
- In flight: nothing. No open worktrees or branches.
- Next (ready now): T-011 rerank/citations/abstain (M), T-012 map-reduce summaries. Use `node --experimental-strip-types scripts/agentic/log-decision.ts` (plain `node` fails on this Node).

## Budget
- BUDGET: day cap raised by the human in budgets.json; guard-budget tracks spend since resume.

## Open (human)
- .env.example: human pasted the 5 model vars; still to document: MAX_UPLOAD_MB semantics (can only lower the 10 MB cap).
- Live LLM later: ANTHROPIC_API_KEY in .env (or Ollama). T-023 evals run on scripted mock answers until then.
- Git history still holds the personal citizenship documents (Later).
- ADR-0001/0002 are still "proposed": review at Gate B.

# Status (rolling, max 60 lines; orchestrator rewrites after every stage)

Updated: 2026-11-07 · Mode: autopilot · Phase: P4 in progress (P0-P3 done; T-020 + T-021 building, T-022 + T-023 pending)

## Decisions made
- Spec (docs/spec.md): P0-P4 backend only. Later items in docs/backlog.md.
- ADR-0001/0002 still "proposed": review at Gate B. Integration branch `dev`; tickets squash-merge directly, NO PRs (CLAUDE.md override).
- No Anthropic key; build and all tests run on LLM_PROVIDER=mock. No paid API is called by agents.
- Use `node --experimental-strip-types scripts/agentic/*.ts` (plain `node` fails on Node 22).

## Progress
- Done on dev (831 tests): T-001..T-019 (P0 foundation + 8 endpoints, P1 hybrid retrieval/rerank/citations/summaries,
  P2 six agent tools, P3 LangGraph agent core + grade/rewrite/groundedness + SSE on POST /v1/query).
- In flight: T-020 per-session memory, T-021 prompt-injection defences (worktrees/t-020-*, t-021-*).
- Next: T-022 rate limit + spend cap + PII-safe logs (after T-020/T-021 merge: shares api/query.py), then T-023 golden set + evals, then security-auditor, then GATE B.

## Budget
- Day cap raised by the human to $45 in .claude/budgets.json; guard-budget passes. No exact spend figure tracked here.

## Open (human)
- .env.example (agents cannot touch it): MIN_RERANK_SCORE=0.0, MAX_UPLOAD_MB (can only lower the 10 MB cap),
  SUMMARY_MAX_USD, SUMMARY_REDUCE_ROLE, SUMMARY_COMPRESS, SUMMARY_BATCH_CHARS, SUMMARY_MAX_BATCHES; check
  AGENT_MAX_STEPS (code default is now 16; a stale value of 6 in .env.example would override it);
  optional AGENT_MAX_TOKENS env var (currently a code constant 30000).
- REQUIRED before the agent is useful on tables: originals are not persisted, so query_table's table_loader is unwired and returns not found. Needs a ticket (persist owner-scoped originals + loader).
- Live LLM later: ANTHROPIC_API_KEY in .env (or Ollama). T-023 evals run on scripted mock answers until then; MIN_RERANK_SCORE and grader accuracy need calibration on real models.
- Git history still holds the personal citizenship documents (Later).
- Uncommitted human edits in the main tree: .env.example, .claude/budgets.json, .claude/settings.json.

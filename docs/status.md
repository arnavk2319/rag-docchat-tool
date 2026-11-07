# Status (rolling, max 60 lines; orchestrator rewrites after every stage)

Updated: 2026-11-06 · Mode: autopilot · Phase: ready for /autopilot (Gate A approved)

## Decisions made
- Spec (docs/spec.md): P0-P4 backend only. Later items in docs/backlog.md.
- ADR-0001 (proposed): FastAPI + SQLite + embedded Chroma + rank-bm25, local MiniLM + bge rerank, hosted Claude via one `llm` wrapper (Ollama optional), LangGraph agent, local-first.
- 23 tickets T-001..T-023, all `approved` by the human (cap $79.50).
- Integration branch is `dev` (kit.json already says so). Human override: tickets squash-merge into `dev` directly, NO PRs (see CLAUDE.md).
- Baseline committed on `dev`. chroma_db/, logs/, worktrees/ are gitignored.

- Human: no Anthropic key for now; old OpenAI key removed. Build and all tests run on `LLM_PROVIDER=mock`. No paid API is called by agents.

## Open (human)
- Live LLM use later: add `ANTHROPIC_API_KEY` to .env (or run Ollama, `LLM_PROVIDER=ollama`). Until then T-023 evals run on scripted mock answers only; real faithfulness/relevance numbers need a live model.
- Git history still holds the personal citizenship documents (Later).

## Next
- Run `/autopilot` (start Claude Code with /opt/homebrew/bin on PATH so uv and gh resolve).

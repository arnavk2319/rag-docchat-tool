# Status (rolling, max 60 lines; orchestrator rewrites after every stage)

Updated: 2026-11-06 · Mode: autopilot · Phase: ready for /autopilot (Gate A approved)

## Decisions made
- Spec (docs/spec.md): P0-P4 backend only. Later items in docs/backlog.md.
- ADR-0001 (proposed): FastAPI + SQLite + embedded Chroma + rank-bm25, local MiniLM + bge rerank, hosted Claude via one `llm` wrapper (Ollama optional), LangGraph agent, local-first.
- 23 tickets T-001..T-023, all `approved` by the human (cap $79.50).
- Integration branch is `dev` (kit.json already says so). Human override: tickets squash-merge into `dev` directly, NO PRs (see CLAUDE.md).
- Baseline committed on `dev`. chroma_db/, logs/, worktrees/ are gitignored.

## Open (human)
- `ANTHROPIC_API_KEY` goes in .env (agents cannot edit it). Rotate/revoke the old exposed OpenAI key.
- Git history still holds the personal citizenship documents (Later).

## Next
- Run `/autopilot` (start Claude Code with /opt/homebrew/bin on PATH so uv and gh resolve).

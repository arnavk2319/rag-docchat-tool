---
description: Consult mode. Ask the orchestrator anything about the project (read-only)
argument-hint: "<question>"
allowed-tools: Read, Grep, Glob, Bash(node scripts/agentic/budget-report.ts:*), Bash(git log:*), Bash(git status:*), Bash(git diff:*), Bash(git branch:*)
---

Read-only: change no files, start no build, open no PR. Answer $ARGUMENTS from the record, cheapest source first, stopping as soon as it is answered: docs/status.md, docs/tickets/*, logs/decisions/*.jsonl, docs/decisions/*, git log/diffs, then code via the Explore subagent (haiku). Cite file paths. Cap 30k tokens; if more is needed say what you would read and ask first. Work that turns up becomes a draft ticket proposal in your reply.

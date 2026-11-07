---
description: Backlog, gates waiting, and spend today and this week (read-only)
allowed-tools: Read, Grep, Glob, Bash(node scripts/agentic/budget-report.ts:*), Bash(git log:*), Bash(git status:*), Bash(git diff:*), Bash(git branch:*)
---

Read docs/status.md and docs/backlog.md, then run `node scripts/agentic/budget-report.ts`. Report in under 15 lines: tickets by status per phase, which gate is waiting on the human, spend today and this week versus caps (flag 70%+), and the next ready ticket. Change no files.

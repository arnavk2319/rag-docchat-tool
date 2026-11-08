---
description: Recent decisions and resource use for an agent or ticket (read-only)
argument-hint: "<agent name | ticket id>"
allowed-tools: Read, Grep, Glob, Bash(node scripts/agentic/budget-report.ts:*), Bash(git log:*), Bash(git status:*), Bash(git diff:*), Bash(git branch:*)
---

For $ARGUMENTS: read the last ~10 entries of logs/decisions/<agent>.jsonl and logs/usage/<agent>.jsonl (for a ticket id, grep it across logs/decisions and logs/usage). Summarise each: when, stage, decision, why, cost, turns. Cite files. Change no files.

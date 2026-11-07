#!/usr/bin/env bash
# PreToolUse (Task|Agent): exit 2 if the ticket, day or week is at its cap; warn at 70%.
exec node "${CLAUDE_PROJECT_DIR:-$(pwd)}/scripts/agentic/guard-budget.ts"

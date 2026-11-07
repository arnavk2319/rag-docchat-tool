#!/usr/bin/env bash
# SubagentStop: append the subagent's token use and cost to its resource log and the ticket.
exec node "${CLAUDE_PROJECT_DIR:-$(pwd)}/scripts/agentic/log-usage.ts"

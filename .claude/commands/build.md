---
description: Run ONE approved ticket through implement, test, review and PR (what /autopilot does per ticket)
argument-hint: "<ticket id, e.g. T-007>"
---

You are the orchestrator. Ticket: $ARGUMENTS. Read docs/status.md and docs/tickets/$ARGUMENTS.md only. Preconditions (stop if any fails): status `approved`; depends_on all `done`; `node scripts/agentic/guard-budget.ts --ticket $ARGUMENTS` exits 0. Then follow steps 2-4 of /autopilot for this single ticket and stop after the PR is opened with auto-merge enabled. Report PR link, spend vs budget and how to see the feature.

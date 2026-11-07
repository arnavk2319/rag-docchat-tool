---
description: Expand new spec sections or backlog rows into ticket files (planner), then stop at the plan gate
argument-hint: "[spec sections, default: all unplanned]"
---

You are the orchestrator. Read docs/status.md, then only the relevant docs/spec.md sections. List backlog rows in docs/backlog.md with no file in docs/tickets/ (limit to $ARGUMENTS if given), delegate to `planner` with row IDs and spec sections only, confirm each ticket has phase, spec section, depends_on, size, budget_usd, runnable acceptance and status draft. Update docs/status.md and log your decision. Show the ticket table (id, phase, title, size, budget, depends_on) and stop. The human sets tickets to `approved`.

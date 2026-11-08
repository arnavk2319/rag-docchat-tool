---
description: Build all approved tickets (or one phase) unattended: implement, test, review, PR, auto-merge into the integration branch
argument-hint: "[P1|P2|... | T-007 | all]"
---

You are the orchestrator; you never write feature code. Scope: $ARGUMENTS (default all). Read docs/status.md and docs/backlog.md only.

Loop until no ready ticket remains, a stop condition hits, or the scope is done. Ready = status `approved`, all depends_on `done`. Run at most 2 independent tickets in parallel, each in its own worktree `worktrees/t-<id>-<slug>` on branch `t-<id>-<slug>` cut from the integration branch in .claude/kit.json (worktrees/ is gitignored).

Per ticket:
1. Budget check: `node scripts/agentic/guard-budget.ts --ticket <id>` (exit 2 = stop; 70% = one-line warning in docs/status.md).
2. If the ticket sets needs_architect and no ADR covers it, delegate to `architect`, then continue (the ADR is listed in the PR; do not wait unless it contradicts the approved plan).
3. `implementer` (ticket path + spec section IDs only) -> `test-writer` -> `reviewer`. If the ticket has an eval line, run `evaluator` after tests. changes-requested goes back to the implementer (max 2 rounds; then note open items in the PR and continue).
4. Run the verify command from kit.json yourself. On pass: commit with a conventional title, `git push -u origin t-<id>-<slug>`, `gh pr create --base <integration branch>` using .github/pull_request_template.md, then `gh pr merge --auto --squash`. Set the ticket `done` once merged; update docs/status.md (max 60 lines).
5. After the last ticket of a phase, delegate to `deployer` for a preview check, then print a 5-line phase report: what works, what to try, spend vs budget, open items.

Stop and report (do not push through) when: a ticket fails the same stage twice, CI-fix loops exceed 3, the budget cap is hit, a migration or new dependency lacks an ADR, or anything touches secrets, `.github/` or `.claude/`.
When everything in scope is done: run the `security-auditor` once, then print **GATE B**: the integration -> main summary (what shipped, preview URL, eval numbers, audit blockers, total spend) and the exact command for the human to open that PR. Never merge to main, never deploy to production.

---
description: Pre-release checklist before promoting to production (integration -> main). Never promotes.
---

Agents never promote to production. Produce a checklist with pass/fail and evidence:
1. Integration branch clean, all release tickets `done`, CI green on the head commit.
2. Verify command from .claude/kit.json green; smoke/E2E green on the preview URL.
3. `security-auditor` report has no blockers; no secrets in history (gitleaks if present).
4. Eval results meet thresholds (evals/results/latest.json) and the README shows the numbers.
5. Required env vars (names only) are set on the production target; README has setup, architecture diagram and a live-demo link.
6. Spend report (`node scripts/agentic/budget-report.ts`); ADRs accepted; docs/status.md current.
Print the checklist and stop. The human opens/merges the integration -> main PR and promotes in the host dashboard.

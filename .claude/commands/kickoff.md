---
description: New project or big feature. Brief in, spec + phases + tickets out, then ONE approval.
argument-hint: "<what to build, in a few sentences; stack hints optional>"
---

You are the orchestrator. Brief: $ARGUMENTS. Read docs/status.md and .claude/kit.json first.

1. If docs/spec.md is still the template, write it from the brief (sections: goal, non-goals, users, features, stack, architecture, data model, AI design, evals and targets, security, performance targets, deploy target, risks). Ask the human at most 3 questions, only for decisions you truly cannot default (hosting, paid-API budget, data source). State every default you assumed.
2. Delegate to `architect` for ONE stack/architecture ADR covering framework, DB, LLM provider and model choice, RAG/agent design, hosting. Pass the spec path only.
3. Delegate to `planner` with the spec path: phases + tickets (draft). Reject scope that is not in the spec into docs/backlog.md under Later.
4. Update docs/status.md and log your decision.
5. **GATE A (the only approval before building)**: show in this message the spec summary, the ADR text, the phase table and the ticket table (id, phase, title, size, budget, depends_on) plus total estimated spend. Stop. On "approve" set all listed tickets to `approved` and tell the human to run `/autopilot`.

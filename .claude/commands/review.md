---
description: Review a branch with the reviewer subagent (read-only)
argument-hint: "<branch, e.g. t-007-search-api>"
---

Delegate to the `reviewer` subagent for branch $ARGUMENTS against the integration branch in .claude/kit.json. Pass the branch name and ticket path only, never pasted diffs. Relay the verdict block and write it into the ticket's Review section. Do not edit code.

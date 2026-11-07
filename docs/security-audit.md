# Security audit of `dev` @ 6ac2220 (2026-11-07)

Read-only audit by the security-auditor agent. Verdict: **fine for local/demo; NOT fit for internet exposure** until real authentication replaces `X-User-Id`.

Scanners: `ruff --select S` gave 9 hits, all false positives (bound-parameter SQL f-strings over constants, retry jitter). `pip-audit` was unavailable offline, so **no dependency audit has been run**: run `uvx pip-audit` with network before any release. All deps are pinned exactly; `uv.lock` exists.

Secrets: no key-like strings tracked; `.env` was never committed (48 commits checked). Rotating the previously exposed OpenAI key is still a human task.

## Must fix before production exposure

| ID | Sev | Where | Issue | Fix |
|---|---|---|---|---|
| B-1 | BLOCKER | api/deps.py `current_user` | `X-User-Id` is trusted: any caller impersonates any user; rotating ids defeats per-user limits (only global caps remain). | Real JWT/session auth, limits bound to the verified subject, per-IP limit at the edge. |
| H-1 | HIGH | POST /v1/documents | No rate limit, per-user doc/byte quota or ingest concurrency cap; CPU (parse + embed) and disk abuse. | Upload limiter, per-owner quotas, caps on pages/rows/chunks, ingest semaphore. |
| H-2 | HIGH | upload body size | 50 MB cap relies on Content-Length; chunked bodies are spooled to disk by Starlette before our checks. | Proxy limit plus streaming-size ASGI middleware; multipart part limits. |
| H-3 | HIGH | zip checks, table_query | Zip-bomb checks trust declared header sizes; no page/sheet-dimension caps; parsing has no timeout. | Streaming decompress with a byte cap, dimension caps, parse in a worker with timeout/memory limit. |
| H-4 | HIGH | main.py | No CORS allow-list, security headers or TrustedHost (matters once cookie auth exists). | Explicit CORS origin, security headers. |
| H-5 | HIGH | main.py | /docs, /redoc, /openapi.json, /healthz detail and /ping-checkpoint are unauthenticated. | Disable docs in prod, trim /healthz, remove or limit /ping-checkpoint. |
| M-1 | MED | src/documents/*, evals/fixtures | Real personal documents tracked and in git history since the initial commit. | Confirm no public remote; `git rm`; rewriting history is a human, destructive decision. (Eval fixtures were made synthetic in T-023; src/documents/ still tracked.) |
| M-2 | MED | .gitignore | `data/`, `chroma_db_bm25`, `*.db` not ignored. | Add them (human: .gitignore edits). |
| M-3 | MED | limits, ledger | Per-process state; multi-worker multiplies limits; unknown model ids price at 0 (silent cap bypass). | Single worker or Redis; fail closed on unknown price. |
| M-4/M-5/M-6 | MED | JSON body cap, documents list, LLM base URL | Content-Length-only JSON cap; list slices in Python; LLM URL env-only (no SSRF today). | Streaming-size middleware; SQL pagination; keep env-only. |

LOW: `tracemalloc.start()` at import (L-1), dead `load_xls` path (L-2), expired sessions purged only at startup (L-4), HF model revisions unpinned (L-5).

## Verified OK
All /v1 routes require `CurrentUser` and are owner-scoped end to end (SQL bound parameters, Chroma owner filter, owner-wide BM25, hashed index paths); uploads use basename-only names and temp-file cleanup; no pickle/eval/exec/subprocess; tools are allow-listed, run owner-scoped, outputs sanitised, citations structural; `query_table` uses a closed enum; cost controls (per-user + global rate and USD caps, reservations settled on disconnect, per-run budgets) are in place before any LLM call.

## Acceptable for local/demo only
Header auth on localhost, in-memory limiters, exposed docs/healthz, missing security headers, tracemalloc, forged-zip risk with trusted uploaders, mock LLM default.

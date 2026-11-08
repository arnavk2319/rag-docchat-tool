# Security audit: P5/P6 delta (anonymous access, web app) - 2026-11-08
Auditor: security-auditor (read-only; code reading + fresh prod build + npm audit; backend not run live, pip-audit unavailable).
Verdict: safe for a public portfolio demo = YES, 0 blockers, with conditions C1-C5.

Closed since docs/security-audit.md: B-1 (X-User-Id trust; HMAC bearer tokens, tenant/owner only from verified credential), H-5 (docs off by default, /healthz trimmed). Partly closed: H-4 (CORS allow-list added; backend security headers still absent). Still open: H-1 (upload quota, now reachable anonymously), H-2, H-3 (Content-Length-only caps, no parse timeouts).

## High
- H1 documents.py:35-62, uploads.py:49-80: no per-owner doc/byte quota, no upload rate limit, no ingest concurrency cap; purge off by default (PURGE_INTERVAL_SECONDS=0). Anonymous identities (10 mints/h/IP) x 50 MB/request fill disk/pgvector. Fix: upload limiter on tenant:user (~5/h), per-owner doc cap (~5) for anon- owners, demo-tenant byte cap, PURGE_INTERVAL_SECONDS=3600.

## Medium
- M1 auth_anon.py:50-58, deps.py:119-126 TRUSTED_PROXY: unset behind a proxy => one global mint bucket (10 mints lock out everyone); XFF uses rightmost value (correct for exactly one trusted hop); directly reachable origin lets attackers spoof XFF. Fix: origin only behind the platform proxy, set TRUSTED_PROXY to its header, add a global mint limiter (~200/h).
- M2 spend-cap DoS (not drain): demo tenant cap 1.0 USD/day, user 0.10; ~10 identities exhaust the demo tenant for the UTC day. Loss bounded at 1 USD/day. Run a single instance (ledger per process). Document.
- M3 ingestion.py:221, uploads.py:91: failure_reason = type(e).__name__ returned to clients. Fix: map unknown exceptions to fixed `ingest_failed`.
- M4 web/public/mockServiceWorker.js (gitignored, from `msw init`) is copied into dist/ by vite build when present. Inert, but dead mock code in prod. Fix: delete before prod build; Vercel build command must be `npm run build` (prebuild already deletes public worker in this repo's script, verify).

## Low / Info
- L1 no backend security headers (nosniff, no-store, HSTS at proxy). L2 compare.py/docintel.py lack json_body_limit (models bounded). L3 web/vercel.json ships placeholder API origin (CSP blocks real API until `API_ORIGIN=... npm run gen:vercel`). L4 list_documents slices in Python (bounded once H1 fixed). L5 pip-audit not installed; Python deps unaudited; image carries CUDA torch, unpinned model revisions.
- Info: /ping-checkpoint unauthenticated (remove). AUTH_TOKEN_SECRET >=32 bytes enforced. Personal docs in git history and exposed OpenAI key (human items) still open.

## Verified clean
Anonymous tokens (TTL 3600 s, random anon- sub, demo tenant only, per-IP mint limit with IPv6 /64 folding, bounded purge scoped to demo tenant); every /v1 route behind CurrentUser/CurrentTenant, uniform 404, chunk endpoint checks doc ownership + chunk doc_id + parent doc_id; llm_guard on all LLM routes (user, global, reserve, finish in finally, disconnect cancel, partial spend); input bounds (question 2000, doc_ids 20, compare 2-5, focus 200, JSON 64 KB); sanitize_output on all model text; no XSS sinks in web/src; token in sessionStorage only; strict CSP; prod bundle has no msw/__mock; npm audit --omit=dev 0 vulns; CORS wildcard stripped, credentials off; error bodies {detail, code}; Dockerfile non-root, no baked secrets.

## Conditions for a public demo
C1 fix H1 (upload limiter + per-owner doc cap) or host quotas; C2 set PURGE_INTERVAL_SECONDS or cron; C3 single trusted proxy + TRUSTED_PROXY, non-public origin; C4 single instance, ENABLE_DOCS=false, exact CORS_ALLOW_ORIGINS, regenerated vercel.json; C5 run pip-audit once.

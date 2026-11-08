# Security audit of `dev` after P5 (T-024..T-033), 2026-11-07

Read-only audit by the security-auditor agent. Baseline: docs/security-audit.md. Scanners: `pip-audit` on `uv export --frozen` (no known vulnerabilities, all deps pinned in uv.lock); secret-pattern grep over tracked files and the P5 history (no keys, no DSNs with passwords; `.env` untracked and in `.dockerignore`). The exposed OpenAI key in the local `.env` remains a human rotation task.

## Verdict

The P5 backend is substantially safer than the audited baseline: real HMAC bearer auth (fail closed on empty/short secret, constant-time compares, uniform 401), hashed API keys, and tenant isolation that holds on every store (Postgres tables, pgvector, registry, sessions, parents, BM25, object store, spend and rate-limit keys); all SQL is parameterised and the LLM endpoints (summary, sentiment, compare, query) check rate limits and USD caps before any model call, sanitise untrusted text, and build citations from server-side records. There are no code-level Blockers. It is safe to expose on staging to invited tenants, with ONE replica, a >=32-byte `AUTH_TOKEN_SECRET`, `ENABLE_DOCS=false`, a TLS-terminating proxy that enforces a request-body size limit and a per-IP rate limit, and a private S3 bucket. It is NOT yet fit for open or untrusted-tenant internet exposure until the upload abuse items (H1-H3), the zero-price spend bypass (M1) and the missing key issuance/revocation path (M4) are closed.

## Prior findings status

| ID | Status | Note |
|---|---|---|
| B-1 | FIXED (with caveat) | HMAC tokens + hashed tenant API keys; identity only from verified credential. Caveat M4: no key issuance/persistence path yet. |
| H-1 | OPEN | Upload still has no limiter/quota (see H1). |
| H-2 | OPEN | Content-Length-only cap (see H2). |
| H-3 | OPEN | Zip/parse limits unchanged (see H3). |
| H-4 | PARTIAL | Env CORS allow-list (no `*`, no credentials) done; security headers and TrustedHost still absent (L4). |
| H-5 | MOSTLY FIXED | Docs/openapi off by default, `/healthz` trimmed unless `ENABLE_DOCS`; `/ping-checkpoint` remains (L3). |
| M-1 | PARTIAL | `src/documents/*` removed from tree; still in git history (human decision). |
| M-3 | OPEN | Unknown model price still 0 (M1); counters still per process (M2); tenant cap added. |
| L-1 | OPEN | `tracemalloc.start()` still at import (L5). |
| L-2 | NOT RE-CHECKED | Dead `load_xls` path. |
| L-3 | n/a | Not present in the prior document. |
| L-4 | OPEN | Postgres sessions are purged at startup only (L7). |
| L-5 | OPEN | HF model revisions unpinned (L6). |

## Findings

### Blocker
None.

### High

| ID | file:line | Impact | Fix | Blocks internet exposure? |
|---|---|---|---|---|
| H1 | src/backend/app/api/documents.py:25 (POST /v1/documents; no limiter/lease) | Any valid user can upload/ingest in a loop: CPU (parse + embed), pgvector/object-store growth, no per-user/tenant quota. | Upload rate limiter, per-owner/tenant doc and byte quotas, ingest semaphore. | Yes for untrusted tenants (edge rate limit is a stopgap) |
| H2 | src/backend/app/api/documents.py:~38 (`content-length` check), api/deps.py:~116 (`json_body_limit`) | Chunked bodies bypass the 50 MB / 64 KB checks and are spooled before our code runs. | Proxy body limit plus streaming-size ASGI middleware. | Yes unless the edge proxy enforces it |
| H3 | src/backend/app/core/security.py:~40-70 (zip checks), loaders | Zip-bomb check trusts declared sizes; no page/sheet caps or parse timeout; memory/CPU DoS by an authenticated uploader. | Streaming decompress with byte cap, dimension caps, worker with timeout/memory limit. | No for staging with trusted uploaders; yes for public |

### Medium

| ID | file:line | Impact | Fix | Blocks? |
|---|---|---|---|---|
| M1 | src/backend/app/core/llm.py:241 (`prices.get(model, (0.0, 0.0))`), core/config.py:12 | A model id not in the price table costs 0, so per-run/user/tenant/global USD caps never trip (silent cap bypass after a model change or Ollama/other provider). | Fail closed (raise / price at a conservative max) on unknown model; test that every configured model id is priced. | Yes (cost control) until fixed or models verified priced |
| M2 | src/backend/app/store/spend.py, core/limits.py, main.py:~86 | Spend, rate limits, parents and BM25 are per process / local SQLite at `/data`; a redeploy without a volume resets the daily cap, a second replica multiplies limits. Documented as "run one replica". | Keep one replica with a persistent volume until moved to Postgres/Redis; enforce in deploy config. | No (one replica + volume) |
| M3 | src/backend/app/api/deps.py:~160 (`glimiter.hit(GLOBAL_KEY)`) | One tenant can consume the shared global rate limit (120/min) and starve others; no per-IP or pre-auth throttling; unauthenticated requests are never limited. | Per-tenant rate bucket below the global one; per-IP limit at the edge. | No for staging |
| M4 | src/backend/app/api/deps.py:46-49 (`InMemoryKeyStore`), core/auth.py:147 | No endpoint/CLI persists or issues API keys; the key store is in memory (lost on restart, not shared, revocations vanish), tokens have no revocation (1 h TTL only) and nothing in-repo mints them. API-key auth is effectively unusable and token minting is an out-of-band, unaudited process. | Postgres-backed KeyStore + admin issue/rotate/revoke tool (human-run); keep TTL short; document the minting service. | Yes for production-grade tenant onboarding; no for staging with tokens minted by the operator |
| M5 | src/backend/app/core/auth.py:25 (`valid_id`), store/scoped.py:~25, vectorstore/base.py:11 | The reserved legacy tenant `default` is a valid tenant id; a credential for tenant `default` maps to bare owner ids (legacy data, no prefix). Not exploitable without the signing secret, but a mis-issued key or token for `default` reads legacy/unscoped data. | Reject `default` in `mint_token`/`issue_api_key`/`verify_*` outside tests; migrate legacy data. | No |

### Low

| ID | file:line | Impact | Fix | Blocks? |
|---|---|---|---|---|
| L1 | src/backend/app/db/migrate.py:28 | Guard checks `urlparse(url).hostname` only; `postgresql://localhost/x?host=remote.example` (libpq param) or a remote host named `db` passes. Needs control of the human's env. | Reject query-string `host`/`hostaddr`/multi-host and require explicit loopback; drop the `db` allowance outside compose. | No |
| L2 | src/backend/app/api/compare.py:16 (no `json_body_limit`), api/schemas.py:142 | `doc_ids` entries have no max length; bodies are not size-capped (count/focus are). | Add `json_body_limit` dependency and `max_length` on ids. | No |
| L3 | src/backend/app/main.py:165 | Unauthenticated `/ping-checkpoint` writes a log line per hit (log flooding). | Remove. | No |
| L4 | src/backend/app/main.py:create_app | No security headers (nosniff, HSTS at edge) or TrustedHost. Low for a bearer-token JSON API. | Add at the proxy. | No |
| L5 | src/backend/app/main.py:27 | `tracemalloc.start()` at import adds memory/CPU overhead (DoS amplifier). | Remove or gate on env. | No |
| L6 | Dockerfile:7,18; core/config.py:34-35 | Base and uv images pinned by tag, not digest; HF embedder/reranker download on first start with unpinned revisions (supply chain). | Pin digests and model revisions; bake or mirror models. | No |
| L7 | src/backend/app/store/pg.py (PostgresSessions) | Expired sessions purged only at startup. | Periodic purge. | No |
| L8 | src/backend/app/api/deps.py:70-72 | A tenant API key can act as any owner id in its tenant via `X-On-Behalf-Of` (by design) with no audit log of the on-behalf-of identity. | Log (tenant, key_id, owner) at info; document the trust model. | No |
| L9 | docs/deploy.md; objectstore/s3.py | No guidance to require `sslmode=require` on `DATABASE_URL`, private bucket, or server-side encryption. | Add to the runbook. | No |

## Verified OK (P5 surface)

- Service auth (core/auth.py, api/deps.py): HMAC-SHA256 with `hmac.compare_digest`; empty or <32 byte secret fails closed; `exp` enforced; ids restricted to `[A-Za-z0-9_.@-]{1,64}` (no `:` or `#`, so scoped keys cannot be forged or collide); credential length capped; API key secrets stored only as keyed HMAC; dummy compare avoids an existence oracle; X-On-Behalf-Of validated and ignored for bearer tokens; tenant comes only from the verified credential.
- Tenant isolation: every Postgres query filters `tenant_id` and `owner_id` (documents, sessions, messages with composite FK, vector_chunks PK); registry/sessions views are tenant-bound (cross-tenant lookups return 404); parents/BM25 use `tenant:owner` keys; rate limit and user spend keys are `tenant:owner`, tenant spend key `#t:<tenant>`; object keys must sit under `tenant/<tenant>/`. The SQLite registry/sessions variants are not tenant-aware but are test/dev only (not wired in `create_app`).
- pgvector SQL: all values bound; the only `.format`/concatenation inserts fixed fragments; vectors are built from `float()` values; ids validated by `check_id`; `doc_id = ANY(%s)`.
- ObjectStore: segment whitelist, `.`/`..` rejected, prefix check plus resolved-path containment (local); S3 key validated; filename reduced to a single safe segment; S3 endpoint is env-only (no SSRF).
- Doc-intel/compare: doc ownership resolved first (foreign/unknown ids are 404 before any LLM call), then per-user + global rate limit and `lease.acquire()` (user, tenant, global USD caps) before the call, run capped by `summary_max_usd`, one LLM call; text passes `sanitize_untrusted`, section indexes (not model text) drive citations (forged citations dropped), outputs pass `sanitize_output`; injection fixtures exist in tests.
- CORS: explicit origins only (`*` filtered), no credentials; docs, openapi and detailed healthz off by default.
- Dockerfile: non-root uid 10001, multi-stage, `--frozen --no-dev`, no secrets baked, `.dockerignore` excludes `.env*`, data, git and tests; single uvicorn worker; EXPOSE 8080 only. docker-compose.yml: Postgres bound to 127.0.0.1, password required from env (`:?`), no inline secret.
- Migration runner: advisory lock, checksum verification, non-local host refusal (see L1 for the bypass).

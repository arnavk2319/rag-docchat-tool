# ADR-0003 Postgres + pgvector service platform, object storage, auth, tenancy
Status: proposed

## Context
P5 turns the single-process app (embedded Chroma at `CHROMA_DIR`, SQLite at `DB_PATH`, `X-User-Id` header; see `docs/ai-design.md` line 7 and spec "Data model") into one containerised service for a web app and for service-to-service use. It needs shared durable state, original-file storage (so `query_table` works), real auth (audit B-1), multi-tenancy and a tenant spend cap. This ADR is the prerequisite for new dependencies (spec P5). It supersedes the Chroma/SQLite lines of ADR-0001/0002 for storage only; embeddings, rerank and prompts are unchanged.

## Decision

### 1. Postgres + pgvector (one datastore)
- Postgres 16 with the `vector` extension holds registry, sessions, chunks, vectors, BM25 metadata, API keys and spend ledgers. One server replaces Chroma + SQLite (fewer moving parts, transactional deletes).
- Dependencies (exact pins, added by the implementer with `uv`; if a pin is unavailable, take the nearest patch and record it in the PR): `psycopg[binary]==3.2.9`, `psycopg-pool==3.2.6`, `pgvector==0.4.1` (registers the vector type for psycopg3), `boto3==1.38.0`. Async via `psycopg_pool.AsyncConnectionPool` (min 1, max from config, opened in app lifespan).
- Vectors: `vector(384)` (all-MiniLM-L6-v2), cosine distance, HNSW index (`vector_cosine_ops`). Chosen over IVFFlat: no training step, good recall on small/medium corpora.
- `VectorStore` interface (Protocol in `vectorstore/base.py`): `upsert_chunks(tenant_id, owner_id, doc_id, chunks, embeddings)`, `query(embedding, k, tenant_id, owner_id, doc_ids=None)` returning chunk + dense score (hybrid-friendly: caller fuses with BM25 scores, so the store returns ids, scores and chunk payload), `delete_doc(tenant_id, owner_id, doc_id)`, `count(tenant_id, owner_id=None, doc_id=None)`. Implementations: `PgVectorStore`, `InMemoryVectorStore` (numpy cosine, used by every unit test; no server needed). A shared contract test runs against the fake; the Pg impl is tested only in an opt-in local-compose marker.
- tenant, owner and doc filters are mandatory parameters, not optional: the interface cannot express an unscoped query.

### 2. BM25 stays in process
`rank-bm25` stays an in-process index per (tenant, owner) built from chunk texts. Postgres persists chunk text plus a per-document tokenised-corpus blob (`bm25_docs` table, bytea, keyed by doc_id) so a restart or second replica rebuilds the index from Postgres without re-parsing or re-embedding; rebuilt lazily and invalidated on upsert/delete. Rationale: no new dependency, identical scoring to current evals. Postgres FTS (`ts_rank`) is not BM25 and would change eval numbers; revisit if one owner's corpus exceeds memory (backlog).

### 3. Registry and sessions move to Postgres
`documents`, `chunks`, `sessions`, `messages` tables carry `tenant_id` and `owner_id` (NOT NULL, indexed together). sha256 dedup becomes unique `(tenant_id, owner_id, sha256)`. Data migration from SQLite is not needed: the current store is local-dev only; users re-upload.

### 4. ObjectStore
Protocol: `put(key, bytes_or_stream)`, `get(key)`, `delete(key)`, `exists(key)`. Impls: `S3ObjectStore` (boto3, works with AWS S3 and Cloudflare R2 via `S3_ENDPOINT`; sync calls wrapped with `asyncio.to_thread`), `LocalObjectStore` (directory under a dev path; default for tests, using `tmp_path`). Selected by `OBJECT_STORE=s3|local`.
- Key scheme: `{tenant_id}/{owner_id}/{doc_id}/{sha256}`. Components are validated against `^[A-Za-z0-9_-]{1,64}$` (sha256 hex 64); the original filename is never part of the key. Any key with `..`, `/` inside a component, absolute path, NUL or a resolved local path escaping the root raises `InvalidKeyError` (local impl also checks `resolved.is_relative_to(root)`).
- `query_table` loads the original from ObjectStore by key; deleting a document deletes object, chunks and vectors in one service call.

### 5. Auth
- User tokens: opaque bearer `v1.<payload_b64url>.<sig_b64url>`; payload `{sub, tenant_id, exp, iat}`; sig = HMAC-SHA256 with `AUTH_TOKEN_SECRET` (min 32 bytes, startup fails if missing in non-dev), constant-time compare, short TTL (default 1 h; anonymous mint endpoint in a later ticket reuses this). Stateless, no per-request DB hit.
- Tenant API keys: format `dck_<key_id>_<secret>`, secret = 32 random bytes (`secrets.token_bytes`), shown once. Stored: `key_id` (public prefix, indexed for lookup), `HMAC-SHA256(AUTH_TOKEN_SECRET, secret)` hex (the secret is high-entropy so a fast keyed hash is sufficient; no password KDF), `tenant_id`, `revoked` bool, `created_at`, `last_used_at`, optional `expires_at`. Rotation: a tenant may hold multiple active keys; issue new, migrate the client, revoke old. Revocation is immediate (checked per request). `last_used_at` updates throttled (at most once/min per key). Auth failures return a uniform 401 without saying which part failed.
- Service-to-service callers send `Authorization: Bearer dck_...` plus `X-On-Behalf-Of: <owner_id>` (owner namespace inside the tenant, validated charset). `X-User-Id` is removed.

### 6. Tenancy rule
`tenant_id` comes only from the verified credential, never from the request body, query or header. Every repository/VectorStore/ObjectStore call takes `tenant_id` first, then `owner_id`, and every SQL statement filters on both. A resource that exists in another tenant (or owner) returns 404, identical to a missing one (no 403, no existence oracle). Cross-tenant isolation tests (two tenants, same sha256, same doc_id guess) are mandatory acceptance for T-029.

### 7. Spend caps
New `tenant_spend(tenant_id, day, usd)` ledger, incremented atomically (`INSERT ... ON CONFLICT DO UPDATE`) from the existing `llm` wrapper cost log. `TENANT_DAILY_USD_CAP` (default 5.00) is checked before each LLM call in addition to the existing per-user `DAILY_USD_CAP` and per-run caps; breach returns 429 `spend_cap_exceeded`. Per-tenant override column is allowed but optional.

### 8. Migrations
Plain versioned SQL files `migrations/0001_*.sql ...` applied in order by a ~60-line runner (`python -m app.db.migrate`) that records applied names plus checksum in `schema_migrations` and takes a `pg_advisory_lock`. Chosen over Alembic: no ORM in the project (raw psycopg), no autogenerate needed, no new dependency, trivially reviewable. The runner refuses to run unless `DATABASE_URL` host is `localhost`/`127.0.0.1`/`db` (compose) or `MIGRATE_ALLOW_REMOTE=1` is set by a human. **Rule: agents never run migrations or any schema push against a non-local database;** staging/prod migrations are applied by the human (or the deploy release step they approve). Migrations are forward-only and additive where possible (expand/contract).

### 9. Local dev
`docker-compose.yml` with `pgvector/pgvector:pg16` (named volume, port 5432 bound to 127.0.0.1) and optional MinIO for S3 testing. `uv run pytest` needs neither; only an opt-in `-m integration` set does.

### 10. Env vars (documented in `.env.example` by the human)
`DATABASE_URL` (postgresql://, secret), `OBJECT_STORE` (`s3|local`, default `local`), `S3_ENDPOINT` (blank for AWS), `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` (secret), `AUTH_TOKEN_SECRET` (secret, >=32 bytes), `TENANT_DAILY_USD_CAP`, `CORS_ALLOW_ORIGINS` (comma list, no `*` in prod), `ENABLE_DOCS` (default false). `CHROMA_DIR` and `DB_PATH` are retired after T-025/T-026.

## Alternatives
- Keep embedded Chroma + SQLite: single-process only, no shared state across replicas, no tenant filtering guarantees, files lost on container redeploy. Rejected for a hosted service.
- Qdrant / Weaviate: strong, but a second datastore to run, pay for and keep consistent with the registry. Rejected (extra ops, no need at this scale).
- Pinecone: managed and paid per index, vendor lock-in, still needs Postgres for the registry. Rejected on cost.
- SQLite-vec: keeps zero infra but single-writer file on ephemeral disk, weak fit for multi-instance. Rejected.
- Postgres FTS instead of in-process BM25: changes scoring and invalidates eval baselines. Deferred.
- JWT (PyJWT): standard, but adds a dependency, tempts `alg` and claim pitfalls, and we need no third-party verification; a small HMAC format is auditable. Revisit if an external IdP is added.
- Alembic: heavier, ORM-oriented; plain SQL + runner suffices.
- Argon2/bcrypt for API keys: pointless for 256-bit random secrets and slow per request.
- Storing uploads in Postgres bytea: bloats the DB and backups; object store is cheaper.

## Consequences (cost, risk, reversibility)
- Cost: a small managed Postgres (~$0-15/mo on free/hobby tiers) plus R2 (free tier, no egress fees); local dev is free. No per-query embedding cost change. Tenant cap bounds LLM spend.
- Risk: pgvector HNSW memory on small instances; Postgres becomes a single point of failure (use provider backups); `AUTH_TOKEN_SECRET` compromise allows forged tokens and API-key hash forgery, so rotation invalidates all tokens (acceptable, short TTL) and requires re-issuing API keys (documented). Bugs in a missed tenant filter are the top data-leak risk, mitigated by non-optional parameters and cross-tenant tests. Pins above should be re-verified at install time.
- Reversibility: `VectorStore` and `ObjectStore` interfaces make backend swaps a single-class change; vectors are re-derivable from stored originals. Migrations are forward-only, so schema rollback means restore from backup. Dropping Chroma is a one-way step once T-025 merges, but the old code stays in git history.

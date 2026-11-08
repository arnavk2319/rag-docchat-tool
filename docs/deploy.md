# Deploy (T-033)

One containerised FastAPI service (Dockerfile at repo root, listens on 8080). Not serverless.

## Branch to environment

| Branch | Service | Who deploys |
|---|---|---|
| `dev` | **staging** | deployer agent may verify; platform auto-deploy from `dev` is set up by the human |
| `main` | **production** | human only |

Promotion `dev` -> `main` and any production deploy is **Gate B: human only**. Agents never push or PR into `main`, never read or write production env vars or secrets.

Staging uses its own database and bucket, never shared with production:
- DB: `docchat_staging` (Postgres with pgvector), role `docchat_staging`; production uses `docchat_prod` / `docchat_prod`.
- Bucket: `docchat-staging-objects`; production `docchat-prod-objects`. Separate S3/R2 access keys per environment.
- `AUTH_TOKEN_SECRET` differs per environment (tokens from staging must not validate in production).

## Image

- Base `python:3.12-slim-bookworm`, multi-stage, deps via `uv sync --frozen --no-dev` (pinned `uv.lock`), runs as non-root uid 10001, no `.env` or secrets baked in (`.dockerignore` excludes `.env*`).
- `HEALTHCHECK` calls `/health`. Default `ENABLE_DOCS=false`.
- Models: **downloaded on first start** into `HF_HOME=/data/hf`, not baked in (keeps image and rebuilds small; no network at build). Mount a persistent volume at `/data` or every restart re-downloads (~1 GB). Revisions are not pinned by the app today (`EMBED_MODEL`, `RERANK_MODEL` are repo ids); pinning a revision is a follow-up. First start takes minutes: the health start-period is 120 s, raise the platform's grace period accordingly.
- Linux `torch` from PyPI bundles CUDA wheels (~4 GB; torch cannot import if they are stripped), so the image is about 10 GB. A CPU-only torch index (~1.5 GB image) changes the lockfile and needs an ADR; it is the top follow-up before paying for registry/storage.

Local check (mock LLM, local object store, no secrets; needs internet on first start to download the models):
```
docker build -t docchat:test .
docker run --rm -e LLM_PROVIDER=mock -e ENABLE_DOCS=false -p 8080:8080 docchat:test
curl -f localhost:8080/health
```
`MODELS_FAKE` is honoured only under pytest; the app refuses to start with it set elsewhere. Without a database it runs in-memory dev mode (fine for this smoke only).

## Environment variables

Secrets are set in the platform secret store, never in the repo or image. Human also adds the P5 vars to `.env.example`.

| Var | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | Postgres+pgvector DSN, secret. Empty = in-memory dev mode (never deploy that way) |
| `POSTGRES_PASSWORD` | local compose only | used by `docker-compose.yml`; managed DBs embed it in `DATABASE_URL` |
| `DB_POOL_MAX` | no (10) | max pool size per pool, per process; keep replicas x 2 x this under the DB connection limit |
| `AUTH_TOKEN_SECRET` | yes | >= 32 bytes, secret; empty makes all auth fail |
| `OBJECT_STORE` | yes | `s3` on staging/prod (`local` is only valid with a persistent volume and one replica) |
| `OBJECT_STORE_PATH` | local only | default `/data/objects` |
| `S3_ENDPOINT` | s3 | R2/MinIO endpoint; empty = AWS |
| `S3_BUCKET` | s3 | per-environment bucket |
| `S3_ACCESS_KEY_ID` | s3 | secret |
| `S3_SECRET_ACCESS_KEY` | s3 | secret |
| `TENANT_DAILY_USD_CAP` | no (2.0) | per-tenant UTC-day cap, must be > 0 |
| `CORS_ALLOW_ORIGINS` | yes for browsers | comma-separated exact origins (the Vercel origin); empty = no CORS; `*` ignored |
| `ENABLE_DOCS` | no (false) | keep `false` on production; may be `true` on staging |
| `LLM_PROVIDER` | yes | `anthropic`, `ollama` or `mock` (mock only for smoke tests) |
| `ANTHROPIC_API_KEY` | if anthropic | secret |
| `AGENT_MODEL`, `FAST_MODEL` | no | model ids, pinned in config defaults |
| `OLLAMA_BASE_URL`, `OLLAMA_AGENT_MODEL`, `OLLAMA_FAST_MODEL` | if ollama | |
| `EMBED_MODEL`, `RERANK_MODEL` | no | HF repo ids |
| `MIN_RERANK_SCORE` | no (0.0) | abstain below this top rerank score |
| `MAX_UPLOAD_MB` | no (10) | lowers the upload cap only |
| `RATE_LIMIT_PER_MIN`, `GLOBAL_RATE_LIMIT_PER_MIN` | no (30, 120) | per-user and global |
| `DAILY_USD_CAP`, `GLOBAL_DAILY_USD_CAP` | no (1.0, 5.0) | per-user and global UTC-day spend caps, > 0 |
| `AGENT_MAX_STEPS`, `AGENT_MAX_USD`, `SUMMARY_MAX_USD` | no | per-run bounds |
| `SUMMARY_REDUCE_ROLE`, `SUMMARY_COMPRESS`, `SUMMARY_BATCH_CHARS`, `SUMMARY_MAX_BATCHES` | no | summarisation tuning |
| `SESSION_MAX_PER_OWNER`, `SESSION_MAX_MESSAGES`, `SESSION_MAX_TOKENS`, `SESSION_TTL_HOURS` | no | session limits |
| `DB_PATH` | no | SQLite file for the parents store (default `/data/docchat.db`), see replica caveat |
| `HF_HOME` | no | model cache, default `/data/hf` |
| `MODELS_FAKE` | never | pytest only; app refuses to start with it set |
| `STAGING_URL`, `STAGING_API_KEY` | deployer smoke | staging only; used by the smoke step |

## Migration (human only)

The service does not migrate on start. A human runs the schema against the target DB from a trusted machine:
```
DATABASE_URL=<staging dsn> uv run python -m app.db.migrate
```
`app.db.migrate` refuses non-local hosts by design; to run it against staging or production a human must deliberately override or apply `migrations/*.sql` with `psql`. Agents never run it. Run staging first, verify, then production at Gate B.

## Rollback

1. Redeploy the previous image or release on the platform (keep the last 3 images tagged by commit SHA).
2. Migrations are forward-only and additive; if one must be undone, restore from the DB snapshot taken before migrating (take one before every production migration).
3. Rotate `AUTH_TOKEN_SECRET` only if it leaked (it invalidates all tokens).

## Reverse proxy settings

- Request body limit: set the proxy cap (nginx `client_max_body_size 55m;`, or platform equivalent) just above the 50 MB multipart total. Starlette spools multipart bodies before the app's 10 MB per-file check, so the proxy is the first line of defence (backlog H-2).
- SSE: disable response buffering for the streaming routes (nginx `proxy_buffering off; proxy_read_timeout 300s;` plus `proxy_http_version 1.1; proxy_set_header Connection "";`; the app already sends `X-Accel-Buffering: no` where supported). Do not enable gzip on `text/event-stream`.
- Run uvicorn behind the proxy with `--proxy-headers` (already in CMD); restrict `--forwarded-allow-ips` to the proxy if it is not the platform default.

## Multi-replica caveats: run ONE replica

Found in T-026: the parents store is still SQLite at `DB_PATH` (local to the container, not shared) and the BM25 index is per process (a second replica sees new documents only after it rebuilds). Rate limit and spend counters are also in process. Until these move to Postgres/shared state, **run exactly one replica** with a persistent `/data` volume, and disable autoscaling. Note that a container without a volume loses parents on redeploy.

## Platform comparison (recommendation: Fly.io)

- Fly.io: Docker-native, persistent volumes, private Postgres (pgvector images work), long-lived SSE fine, scale-to-one easy, per-app secrets, per-branch apps via two `fly.toml`. Best fit.
- Railway: simplest UX, GitHub branch-to-environment mapping built in, volumes and Postgres plugin (pgvector template exists); costs less predictable, weaker health-check controls.
- Render: branch auto-deploys and managed Postgres (pgvector supported), persistent disks paid; free tier sleeps (bad for 1 GB model load), single-instance disks block zero-downtime deploys.
- Recommendation: Fly.io for two apps (`docchat-staging` from `dev`, `docchat-prod` from `main`), 1 machine each with a 3 GB+ RAM size, a volume at `/data`, DB on Neon/Supabase or Fly Postgres with pgvector, objects on Cloudflare R2.

## Human-only checklist

1. Create the platform project/apps (staging and production), the staging DB and bucket, the production DB and bucket.
2. Set secrets in each platform (`DATABASE_URL`, `AUTH_TOKEN_SECRET`, `S3_*`, `ANTHROPIC_API_KEY`) and plain env vars from the table. Never from agents.
3. Add the `.github/` workflow (agents cannot edit it): deploy `dev` to staging, `main` to production, and a drift step before deploy:
   `uv run python scripts/export_openapi.py && git diff --exit-code docs/openapi.json`
4. Add the P5 env vars (`DATABASE_URL`, `DB_POOL_MAX`, `OBJECT_STORE`, `OBJECT_STORE_PATH`, `S3_*`, `AUTH_TOKEN_SECRET`, `TENANT_DAILY_USD_CAP`, `CORS_ALLOW_ORIGINS`, `ENABLE_DOCS`, `POSTGRES_PASSWORD`) to `.env.example`; replace `CHROMA_DIR`.
5. Run the migration against staging, then production (see above).
6. Configure the proxy body limit and SSE settings, and the health-check grace period.
7. Set `STAGING_URL` and a staging tenant key for the deployer smoke; never a production one.
8. Promote `dev` -> `main` and deploy production: **Gate B, human only**.

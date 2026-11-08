# Anonymous data cleanup (T-036)

`services/cleanup.py::purge_expired_anonymous` deletes documents of owners `anon-*` in the demo tenant
(`DEMO_TENANT_ID`) created more than `ANON_DATA_TTL_HOURS` (default 24) ago, via `IngestionService.delete`
(vectors, parents, BM25, stored original, registry row), then purges expired demo-tenant sessions.
Bounded by `PURGE_BATCH_SIZE` per run, idempotent, per-document failures logged as short doc-id hash +
error class and retried next run. Other tenants and non-`anon-` owners are never touched.

Triggers (no scheduler dependency):
- Cron / platform scheduler (recommended): `uv run python scripts/purge_anonymous.py` hourly; exit 1 if any delete failed.
- In-process: `PURGE_INTERVAL_SECONDS>0` starts a cancellable asyncio loop in the lifespan (single-instance
  deploys only); `PURGE_ON_START=true` runs one pass at startup. Both default off.

Note: the SQLite registry is the legacy single-tenant store (documents carry the default tenant), so the purge is a no-op there; it only does work on Postgres/in-memory (dev only on SQLite).

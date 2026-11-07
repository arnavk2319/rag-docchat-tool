# Document registry

`app/store/registry.py`: `Registry` Protocol + `SqliteRegistry` (stdlib `sqlite3`, WAL, parameterized SQL only).

- Path from `DB_PATH` (config) or an explicit argument; parent dir and schema are created idempotently on open.
- `Document`: doc_id, owner_id, filename (original), mime, size, sha256, pages_or_sheets, created_at, status (`pending|processing|ready|failed`, also enforced by a SQL CHECK constraint).
- `created_at` is stored as an ISO-8601 UTC string.
- Methods: `create` (returns doc_id), `create_if_absent` (returns `(Document, created)`), `get`, `get_by_sha256`, `list_by_owner`, `delete`, `update_status`. All take `owner_id`; a foreign owner gets `None` / `[]` / `False`, never an error.
- `list_by_owner` returns docs oldest first, ordered by `(created_at, doc_id)`.
- Dedupe: unique index on (owner_id, sha256) + `INSERT OR IGNORE`; `created` is True only when that insert changed a row (same transaction, race safe), so callers (T-006) can skip embedding on a hit. `create` is a thin wrapper over `create_if_absent`. Different owners get separate docs.
- A dedupe hit returns the existing row whatever its status and never overwrites it. A failed or stuck `pending` doc is returned until the caller resets it with `update_status`; retry policy belongs to the ingestion ticket (T-006).
- Schema version: `PRAGMA user_version = 1`. Future migrations key off `user_version`; there is no migration code yet.
- Threading: one short-lived connection per call (thread-safe). Calls are synchronous; async code must wrap them in `run_blocking`.
- Invalid status raises `ValueError` (update) or pydantic `ValidationError` (create). Deleting vectors in Chroma is the caller's job.

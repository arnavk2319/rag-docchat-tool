# HTTP API (T-007)

Contract source: [ADR-0002](../decisions/ADR-0002-api-contracts.md). Code: `src/backend/app/api/`. Interactive schema: `/openapi.json`.

## Auth (temporary)
Every `/v1` route requires header `X-User-Id` (becomes `owner_id`). Missing or blank -> 401 `auth_required`; longer than 64 chars or outside `[A-Za-z0-9_.@-]` -> 401 `invalid_user_id`. Anyone can claim any id; real auth replaces only `api/deps.current_user`. Tests use `app.dependency_overrides`.

## Endpoints
| Method and path | Notes |
|---|---|
| POST /v1/documents | multipart field `files`, 1..10 files, 10 MB each, 50 MB per request. Always 200 `{results:[UploadResult]}` (per-file `ok`, `doc_id`, `status`, `deduplicated`, `failure_reason`, `error{code,detail}`). Ingestion is synchronous. Request faults: 400 `no_files`/`too_many_files`, 413 `too_large`. |
| GET /v1/documents | `?limit=50&offset=0` (limit 1..200) -> `{items:[DocumentOut], total}` |
| GET /v1/documents/{doc_id} | `DocumentOut` (includes nullable `failure_reason`); 404 for unknown or other owners |
| DELETE /v1/documents/{doc_id} | 204; 404 unknown/foreign (never 403); 409 `document_busy` while ingesting |
| POST /v1/summarize | `{doc_id}` xor `{doc_ids}` (else 422) -> `{summary, doc_ids}`; `fast` role, single shot over the document's stored parent texts in document order (`SqliteParents.list_doc_parents`: page, sheet, char_start; capped at 24000 chars total, truncation noted in the prompt); 404 unknown/foreign, 409 `not_ready` |
| POST /v1/query | `{question, doc_ids?, top_k?=5, session_id?, stream?}` -> `{answer, citations[], abstained, session_id?}`; dense retrieval over child chunks, owner-filtered; the prompt gets the deduplicated parent texts (best child first), citations point at the best child chunk (`<doc>:<p>:<c>`); `agent` role |
Unversioned: `GET /healthz`, `GET /ping-checkpoint`, `/openapi.json`.

Per-file upload error codes: `bad_extension`, `empty_file`, `too_large`, `content_mismatch`, `bad_content_type`, `parse_error`, `empty_document`, `ingest_failed`. `deduplicated` is true when the same bytes were already `ready` for this user.

Citation: `{doc_id, filename, chunk_id, page?, sheet?, span{char_start,char_end}}`, built from the retrieved chunks' metadata; filename comes from the registry.

## Errors
Every error body is `{detail, code}`; validation errors (422 `validation_error`) never echo input. Mapping: `BudgetExceeded` 429 `budget_exceeded`, `LLMTransportError` 502 `llm_unavailable`, `LLMValidationError` 502 `llm_bad_output`, `DocumentBusyError` 409 `document_busy`, anything else 500 `internal` (no exception text). JSON bodies over 64 KB -> 413. A multipart part without a filename is a validation error (422 validation_error), not a per-file result.

## Placeholders
- `session_id` and `stream` are accepted by `QueryRequest` but ignored until T-020 (sessions) and T-019 (SSE agent stream); `session_id` is not echoed yet.
- `abstained` is true only when retrieval yields nothing (no LLM call); score-based abstention comes with T-010 reranking.
- `doc_ids` on `/query` is applied by looping per doc until the list filter in T-010. Rate limits and the daily spend cap (429 `rate_limited`, `spend_cap_exceeded`) arrive in T-022.
- `/summarize` is single-shot; map-reduce comes in T-012.

## Services
Created in the app lifespan on `app.state` (registry and parents from `DB_PATH`, Chroma from `CHROMA_DIR`, `IngestionService`); a test may pre-set any of them or override `get_registry`/`get_store`/`get_ingestion`.

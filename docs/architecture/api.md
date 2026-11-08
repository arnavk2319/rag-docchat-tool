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
| GET /v1/documents/{doc_id}/chunks/{chunk_id} | Cited passage for the source pane (T-048): `{doc_id, chunk_id, filename, page?, sheet?, text, highlight{start,end}}`. `text` is the parent passage (capped at 4000 chars, windowed to keep the chunk when longer) returned raw as JSON data (client must render as text); `highlight` are offsets into `text` with `0<=start<end<=len(text)`. Same auth/tenant/owner scoping as other routes, no LLM. 404 `not_found` for unknown doc, unknown chunk, other owner or other tenant (never 403). |
| DELETE /v1/documents/{doc_id} | 204; 404 unknown/foreign (never 403); 409 `document_busy` while ingesting |
| POST /v1/summarize | `{doc_id}` xor `{doc_ids}` (else 422) -> `{summary, doc_ids}`; `fast` role, single shot over the document's stored parent texts in document order (`SqliteParents.list_doc_parents`: page, sheet, char_start; capped at 24000 chars total, truncation noted in the prompt); 404 unknown/foreign, 409 `not_ready` |
| POST /v1/query | `{question, doc_ids?, top_k?=5, session_id?, stream?}` -> `{answer, citations[], abstained, session_id?}`; dense retrieval over child chunks, owner-filtered; the prompt gets the deduplicated parent texts (best child first), citations point at the best child chunk (`<doc>:<p>:<c>`); `agent` role |
Unversioned: `GET /healthz`, `GET /ping-checkpoint`, `/openapi.json`.

Per-file upload error codes: `bad_extension`, `empty_file`, `too_large`, `content_mismatch`, `bad_content_type`, `parse_error`, `empty_document`, `ingest_failed`. `deduplicated` is true when the same bytes were already `ready` for this user.

Citation: `{doc_id, filename, chunk_id, page?, sheet?, span{char_start,char_end}}`, built from the retrieved chunks' metadata; filename comes from the registry.

## Errors
Every error body is `{detail, code}`; validation errors (422 `validation_error`) never echo input. Mapping: `BudgetExceeded` 429 `budget_exceeded`, `LLMTransportError` 502 `llm_unavailable`, `LLMValidationError` 502 `llm_bad_output`, `DocumentBusyError` 409 `document_busy`, anything else 500 `internal` (no exception text). JSON bodies over 64 KB -> 413. A multipart part without a filename is a validation error (422 validation_error), not a per-file result.

## Placeholders
- `session_id` is accepted by `QueryRequest` but ignored until T-020 (sessions); not echoed yet.
- `abstained` is true only when retrieval yields nothing (no LLM call); score-based abstention comes with T-010 reranking.
- `doc_ids` on `/query` is applied by looping per doc until the list filter in T-010. Rate limits and the daily spend cap (429 `rate_limited`, `spend_cap_exceeded`) arrive in T-022.
- `/summarize` is single-shot; map-reduce comes in T-012.

## Services
Created in the app lifespan on `app.state` (registry and parents from `DB_PATH`, Chroma from `CHROMA_DIR`, `IngestionService`); a test may pre-set any of them or override `get_registry`/`get_store`/`get_ingestion`.

## Upload validation (T-008)
Per file, in order: sanitized basename (both separators, control chars dropped, max 255; client name never used for the temp path) -> extension allowlist (pdf, docx, txt, csv, xlsx; legacy xls is rejected 415) -> streamed read capped at `MAX_UPLOAD_MB` (default 10; it can only lower the cap below the fixed 10 MB ceiling, never raise it; read stops at the first chunk over the cap) -> content sniff must agree with the extension (zip member/size limits) -> temp file (random name) removed in `finally`, including parser failure.
HTTP status: body is always the per-file `{results}` shape. If every file failed validation the status is 413 (any `too_large`) or 415 (`bad_extension`, `content_mismatch`, `bad_content_type`); mixed batches and `empty_file` stay 200 with per-file errors. Request-level: 413 over 50 MB total.

## Agent SSE (T-019)
`POST /v1/query` with `stream=true` runs the bounded agent (`run_agent`) and answers `200 text/event-stream` with `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Auth (401), unknown/foreign `doc_ids` (404), not-ready (409) and validation (422) are plain JSON errors raised before the stream opens. No ready docs: the stream opens and ends with an abstained `final`. Frame: `event: <name>\ndata: <one-line JSON>\n\n`; every payload carries `run_id` and a monotonic `seq` from 1. `: ping` comment every 15 s while idle (no seq).

| event | payload |
|---|---|
| `step` | `{node: router\|plan\|tool\|grade\|rewrite\|answer\|groundedness, n, summary}`; `summary` is a fixed label, never document text |
| `tool_call` | `{tool, args}`; args validated against the tool schema, `doc_id(s)` limited to the caller's resolved docs, strings escaped and cut at 200 chars; unknown tools show as `unknown` with `{}` |
| `tool_result` | `{tool, ok, n_items}`; never raw text |
| `token` | `{text}`; word chunks of the final answer, sent only after the groundedness check |
| `citations` | `{citations:[Citation]}` chunk-level, the same shape as the non-stream path: `{doc_id, filename, chunk_id, page?, sheet?, span{char_start,char_end}}`. BREAKING (T-037): was doc-level `{doc_id, filename, span: str}`; `span` is now an object |
| `final` | `{answer, citations, abstained, stopped_reason, usage:{steps,tokens,usd}}`; `stopped_reason` is `done\|max_steps\|max_tokens\|max_usd\|abstained\|error`; always last |
| `error` | `{code, detail}` fixed strings (`llm_unavailable`, `internal`), emitted just before a `final` with `stopped_reason:"error"` |

Model and tool text (answer, tokens, filenames, args) has `<`, `>`, `&` escaped. Budget and step stops are `final.stopped_reason`, never a 500. Disconnect (polled each second, or generator close) cancels the agent task, so no further `llm` calls run. Stream citations are built server-side (`api/sse.py:build_citations`) from chunk ids that tools surfaced for docs the answer names (T-016 rule), re-read tenant-scoped from the vector/parent store (unknown or foreign ids dropped; a named doc with no surfaced chunk falls back to its first stored parent; max 8); ids, filename and sheet are escaped, no chunk text is emitted. Disconnect also sets a per-run `threading.Event` (`Budget.cancel`) that `llm.complete` and `summarize_documents` check before every LLM call, so worker-thread tools (summarize/compare) start no new call after cancel; the call already in flight finishes. An unrecognised agent stop reason is reported as `stopped_reason:"error"`, never `done`. Code: `api/sse.py`; the agent emits raw events through an optional per-run `on_event` hook (`run_agent(..., on_event=)`), sanitised and numbered in `api/sse.py`.

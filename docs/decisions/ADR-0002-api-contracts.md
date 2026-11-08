# ADR-0002 HTTP API contracts (documents, query, agent SSE, sessions)
Status: proposed

## Context
ADR-0001 fixes the stack, layout and `llm` wrapper but not the wire contracts. T-007 (five endpoints), T-019 (agent SSE) and T-020 (sessions) need them fixed once so routers, tests and a later frontend agree. Existing facts: ingestion is `async` and returns a `Document` (status `ready|failed|processing`); `DocumentBusyError` on delete mid-ingest; failure reasons (`parse_error`, `empty_document`, `cancelled`, class name) are only logged today; `llm` raises `BudgetExceeded`, `LLMTransportError`, `LLMValidationError` (all `LLMError`); file `ParseError` lives in the legacy `utilities/file_handler` and moves with the parser.

## Decision
Each item: choice | rationale.

**1. Auth (temporary).** `api/deps.py: current_user(x_user_id: str | None = Header()) -> UserId`. Missing/blank -> 401 `auth_required`; invalid value (len > 64 or not `^[A-Za-z0-9_.@-]+$`) -> 401 `invalid_user_id` | no oracle about which users exist. `owner_id` = that value. Routers only `Depends(current_user)`, never read headers themselves. Later swap: replace this one function (JWT/cookie returning the same `UserId`); tests use `app.dependency_overrides` | zero router churn. TEMPORARY: any caller can impersonate anyone; real auth is the Later phase.

**2. Endpoints** (all under `/v1`, see 6; models in `api/schemas.py`, one per entity; every error body is `Error{detail:str, code:str}`).

| Method/path | Request | Success | Errors |
|---|---|---|---|
| POST /documents | multipart, field `files` (1..10 files) | 200 `UploadResponse{results:[UploadResult]}` | 400 `no_files`/`too_many_files`, 413 request > 50 MB, 413/415 when every file is rejected (see below) |
| GET /documents | `?limit=50&offset=0` (limit max 200) | 200 `DocumentList{items:[DocumentOut], total}` | - |
| GET /documents/{doc_id} | - | 200 `DocumentOut` | 404 `not_found` |
| DELETE /documents/{doc_id} | - | 204 | 404 `not_found` (also other owners' ids, never 403), 409 `document_busy` (DocumentBusyError) |
| POST /summarize | `SummarizeRequest{doc_id? \| doc_ids?}` exactly one (else 422) | 200 `SummarizeResponse{summary, doc_ids}` | 404 any id unknown/foreign, 409 `not_ready` unless status ready |
| POST /query | `QueryRequest{question (1..2000), doc_ids?, top_k? (1..20, default 5), session_id?, stream?=false}` | 200 `QueryResponse{answer, citations:[Citation], abstained, session_id?}`; `stream=true` returns SSE (4) | 404 unknown/foreign doc_id or session_id, 409 `not_ready` |

All endpoints also return 401 (section 1). Details:
- `DocumentOut{doc_id, filename, mime, size, sha256, pages_or_sheets, status, failure_reason?, created_at}`. `Citation{doc_id, filename, chunk_id, page?:int, sheet?:str, span:{char_start,char_end}}` (one of page/sheet, or neither for docx/txt).
- `UploadResult{filename, ok, doc_id?, status?, deduplicated, error?:{code, detail}}`. Codes: `bad_extension`, `empty_file`, `too_large`, `content_mismatch`, `bad_content_type`, `parse_error`, `empty_document`, `ingest_failed`. `deduplicated` = ingest returned an existing `ready` doc.
- **Partial success: HTTP 200** with per-file `ok`; amended by T-008: if every file is rejected by validation the status is 413 (any `too_large`) else 415 (`bad_extension`, `content_mismatch`, `bad_content_type`) while the body keeps the per-file results; mixed batches and `empty_file` stay 200. Original rationale: (not 207, not all-or-nothing) | one bad file must not discard nine good ones; 207 is poorly supported by clients/OpenAPI. Only request-level faults are 4xx.
- `failure_reason`: new nullable `documents.failure_reason TEXT` (registry `user_version` 2, `ALTER TABLE ADD COLUMN`, written by `_fail`; code strings only, never text) | the reason is otherwise lost after the request.
- `doc_ids` omitted on `/query` = all of the caller's `ready` docs. Pagination now | avoids a breaking change later.
- `/summarize` accepts single `doc_id` (T-007 wording) but always answers with `doc_ids` list | one response shape.

**3. Ingestion is synchronous in POST /documents** (request awaits `IngestionService.ingest`, files sequential, each already off the event loop via `run_blocking`) | the 10 MB cap and the 10 s per 20-page PDF target fit a request, and the single-process design has no worker queue; background tasks add a lost-on-restart state machine for no gain. Limits: 10 files, 10 MB each, 50 MB per request. Tests need no polling: TestClient returns after `ready`/`failed`, using `LLM_PROVIDER=mock` and a stub embedder fixture. `status` stays in `DocumentOut` so a later async mode (202 + polling GET) is non-breaking. Client disconnect cancels ingest (existing: marked `failed: cancelled`).

**4. Agent SSE** (`POST /v1/query` with `stream=true`; `text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`). Frame: `event: <name>\ndata: <one-line JSON>\n\n`; every payload has `run_id` and monotonic `seq`.

| event | payload |
|---|---|
| `step` | `{node:"router\|plan\|tool\|grade\|rewrite\|answer\|groundedness", n, summary}` (short label, never document text) |
| `tool_call` | `{tool, args}` (args validated; doc_ids only from caller's set) |
| `tool_result` | `{tool, ok, n_items}` (no raw text; results are untrusted data) |
| `token` | `{text}` answer deltas, emitted only for the final answer after the groundedness check |
| `citations` | `{citations:[Citation]}` |
| `final` | `{answer, citations, abstained, stopped_reason:"done\|max_steps\|max_tokens\|max_usd\|abstained\|error", usage:{steps,tokens,usd}, session_id?}`; always the last event on success or budget stop |
| `error` | `{code, detail}` for unrecoverable faults (mapped per 6), emitted before a closing `final` with `stopped_reason:"error"` |

T-019 names `tool`; this ADR splits it into `tool_call`/`tool_result` (ticket wording to be updated). Budget stops are a `final` with `stopped_reason`, never a 500. Heartbeat: SSE comment `: ping` every 15 s. Disconnect: poll `request.is_disconnected()` and handle `CancelledError`, cancelling the graph task so no further `llm` calls run. Auth, 404 (doc/session) and 422 are plain JSON errors before the stream opens; after it opens, status is 200 and failures are events.

**5. Sessions** (same SQLite file; created with registry `user_version` 2). Tables: `sessions(session_id PK, owner_id, created_at, updated_at, expires_at)` and `session_messages(id PK, session_id FK ON DELETE CASCADE, role CHECK user|assistant, content, citation_ids JSON, created_at)`. Creation is explicit: `POST /v1/sessions` -> 201 `{session_id}` (uuid4 hex); the client passes it as `QueryRequest.session_id` and responses echo it; omitted = no memory | no hidden state. `GET /v1/sessions/{id}` returns the messages and `DELETE` returns 204; both 404 for other owners. Bound: last 10 turns (20 messages) and about 2000 tokens (`_approx_tokens`), oldest dropped first on write and read; `SESSION_MAX_MESSAGES`, `SESSION_MAX_TOKENS`. Stored: question, answer and citation ids only, never retrieved chunks or tool output. TTL 24 h idle (`SESSION_TTL_HOURS`), purged lazily on access and at startup. History enters the prompt as delimited data, not instructions.

**6. Common rules.** Prefix `/v1` (cheap now, avoids breaking a later frontend); `/ping-checkpoint` and `/openapi.json` stay unversioned. Limits: 10 MB per file streamed (existing `read_limited`), 50 MB per request (`Content-Length` check plus running total), JSON bodies <= 64 KB. OpenAPI tags: `documents`, `query`, `sessions`, `health`. T-022 will add `X-RateLimit-Limit/Remaining/Reset` and `Retry-After`; 429 `rate_limited`, daily spend cap 429 `spend_cap_exceeded` (reserved now). Error mapping: `BudgetExceeded` -> 429 `budget_exceeded` (SSE: `final`); `LLMTransportError` -> 502 `llm_unavailable`; `LLMValidationError` (the "ParseError" of the brief; distinct from the per-file `parse_error`) -> 502 `llm_bad_output`; `DocumentBusyError` -> 409; unhandled -> 500 `internal` with no exception text. A handler normalises `HTTPException` and 422 into `{detail, code}` (`validation_error`, input not echoed).

**7. Legacy removal.** `routers/response.py` and `POST /validate-upload` are deleted in T-007. Security logic moves to `api/uploads.py` / `core/security.py` with unchanged behaviour: extension allowlist `EXT_TO_MIMES`, `sniff_mime` incl. zip limits (`MAX_ZIP_MEMBERS`, `MAX_ZIP_UNCOMPRESSED`), `read_limited` streamed 10 MB cap, extension/content agreement, sanitised filename (basename only; temp file name independent), temp file removed in `finally`. T-003 tests are ported, not dropped. File `ParseError` moves with `parse_uploaded_file`.

## Alternatives
- 207 Multi-Status or all-or-nothing upload: rejected (client support / wasted work).
- Background ingestion + polling now: rejected (extra state; reversible later via `status`).
- Unversioned paths: cheaper but breaking later. Auto-created sessions: hidden state, harder to delete.
- WebSocket for the agent: SSE is one-way, simpler, spec-mandated.

## Consequences (cost, risk, reversibility)
Cost: no new dependency (`StreamingResponse`), $0. Risk: header auth is spoofable (temporary, local only); synchronous upload holds a request up to about 10 s per file, acceptable for one user. Reversibility: schema changes are additive (`failure_reason`, two session tables); `/v1` and `status` keep async ingestion non-breaking. Orchestrator should update T-019 wording (`tool` split) and T-007 response shapes (`abstained`, `doc_ids`).

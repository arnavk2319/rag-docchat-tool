# Sessions (per-session memory, T-020)

Design: ADR-0002 section 5. Explicit and owner-scoped; omitted `session_id` means no memory.

- Endpoints: `POST /v1/sessions` (201 `{session_id}`), `GET /v1/sessions/{id}` (messages), `DELETE` (204). Unknown, foreign and expired ids are all 404 `not_found` (existence never revealed).
- Store `store/sessions.py` (`SqliteSessions`, same SQLite file, tables `sessions`, `session_messages`; `CREATE IF NOT EXISTS`, so safe on an existing registry DB at `user_version` 2). Every statement filters on `owner_id`.
- Stored: question text, answer text, citation ids (chunk ids non-stream, doc ids stream). Never retrieved chunks or tool output.
- Bounds (`SESSION_MAX_MESSAGES`=20 i.e. 10 turns, `SESSION_MAX_TOKENS`=2000 via `_approx_tokens`): oldest whole turn dropped first, on write (rows deleted) and on read. A single message is capped at the token budget in chars.
- TTL `SESSION_TTL_HOURS`=24 idle (each stored turn renews). Purged lazily on every access and at startup.
- Prompt: `services/sessions.history_block` renders turns as an escaped `<conversation>` block (marked as data, not evidence), prepended to the question text in the non-stream answer prompt and in every agent prompt (`nodes._q`). Follow-up resolution is prompt-only; retrieval still uses the new question text. No extra LLM call.
- `/v1/query`: `session_id` validated (404) before any stream opens; response and SSE `final` echo it. The turn is stored after a completed answer only: not for abstentions, budget stops or errors, and a store fault never fails a finished answer.

# Prompt-injection and output-rendering defences (T-021)

Threat: text inside uploaded documents (retrieved chunks) or tool output tries to steer the model:
override instructions, forge tool calls or delimiters, impersonate roles, request other users' documents,
forge citations, or exfiltrate data through rendered markdown/HTML (image or link fetches).
Principle: do not rely on the model to resist; make every injected action fail in code.

## Layers

1. Sanitiser, `core/security.py` (single implementation; `tools.wrap`, `api/rag.escape`,
   `services/compression._escape` and the SSE emitter all call it):
   - `strip_invisible`: drops control chars, zero-width, bidi and other format chars (keeps `\n`, `\t`).
   - NFKC normalisation so full-width look-alike brackets (`U+FF1C`) become `<` and are then escaped.
   - `defang`: markdown images/links/reference definitions, `http(s)/ftp/data/javascript` URLs and `www.` hosts become
     `[image removed]` / `[link removed]` / `[url removed]`; lines starting with `system:`, `assistant:`,
     `user:` etc. are rewritten to `system (quoted text) -:`.
   - `escape`: `&`, `<`, `>` so a payload cannot close `</tool_result>`, `</excerpt>`, `</question>` or forge new tags.
   - Escaping policy: `html.unescape` once, then sanitise, then escape exactly once, so the pipeline is idempotent
     (`R&D` and `R&amp;D` both come out as `R&amp;D`; `&lt;script&gt;` and `&#60;script&#62;` never become live tags or
     `&amp;lt;` garbage). Clients get HTML-safe text.
   - Bounds: input cut to 20 000 chars before any regex, and capped again AFTER escaping (escaping can grow text 5x;
     never cut mid-entity). Every regex quantifier is bounded (worst hostile 20k input ~35 ms, test limit 0.5 s).
     Tool results are capped again at 6000 chars with a `[truncated]` marker.
   - URLs and links are ALWAYS removed from answers, including inside code fences and tables; code samples containing
     `[a](b)` are rewritten (`[a] (b)`/`[link removed]`). Accepted trade-off.
   - Session memory (T-020): `history_block` sanitises stored turns at every replay; storage is unchanged.
   - Paths covered: tool results, compare excerpts, agent question/draft/history args, `/v1/query` excerpts, question,
     non-stream answer, summarize inputs (map and reduce) and summary response, SSE answer and spans.
   - `sanitize_output` (same pipeline) runs on the final answer text and citation spans before they leave the API, so a
     markdown/HTML-rendering client can never fetch an attacker URL.
2. Delimiters + prompts: retrieved/tool text is always inside `<tool_result>`, `<excerpt>`, `<passage>`, `<section>`,
   `<partials>`, `<excerpts>`, `<draft_answer>` or `<question>`; every `prompts/*.md` states this text is DATA, never
   instructions (`agent_router` bumped to v2; a test asserts every prompt says so).
3. Capability limits (hold even if the model fully complies): tool names are allow-listed (`TOOLS`); args models use
   `extra=forbid` so `owner_id` cannot be passed; owner and doc scope come only from `ToolContext`; foreign doc ids
   resolve to not-found; citations are built structurally from tool-surfaced doc ids, never parsed from text; per-run
   step/token/USD budgets.

## Test and eval

`tests/test_injection.py` runs 10 payload docs (`tests/fixtures/injection_*.txt`: ignore-instructions, fake tool call,
markdown/HTML exfiltration, delimiter break, role impersonation, unicode/zero-width obfuscation, forged citation,
foreign doc request, oversized, tool-name injection) end to end through the real agent graph. The mock LLM always
complies (calls the injected tool, requests another owner's doc, cites a forged doc_id, emits image/link/HTML). A payload
is blocked when: only allow-listed tools ran, no foreign doc was read and its secret never reached a prompt or answer,
no forged/foreign citation, rendered output has no link/image/HTML, and sanitised tool results contain no invisible
chars, role markers, URLs or extra delimiters. Mutation checks: disabling owner scope or the tool allow-list makes the corresponding tests fail (tests
`test_mutation_*`); disabling the sanitiser drops the rate to 0.0. Block rate 10/10 = 1.00 (threshold `safety.injection_block_rate` 0.9 in
`evals/thresholds.json`; the test fails below it and prints `EVAL injection_block_rate=...`).

## Abuse limits and logging (T-022)

- **Order**: auth -> per-user rate limit -> daily cap -> (404/409 checks) -> LLM. Limit rejections are plain JSON
  `{detail, code}` 429s raised before any SSE stream opens or LLM call, on `/v1/query` (JSON and stream) and
  `/v1/summarize`. Headers on every limited-endpoint response: `X-RateLimit-Limit/Remaining/Reset`; rejections add `Retry-After`.
- **Rate limit** (`core/limits.py`): sliding 60 s window, `RATE_LIMIT_PER_MIN` (default 30) per authenticated owner id,
  in-process. Memory is bounded: idle users are swept each window and at most 10 000 users are tracked (new ids refused when full of active users), so spraying
  `X-User-Id` values cannot grow state. Code `rate_limited`.
- **Daily spend cap**: `DAILY_USD_CAP` (default 1.0, must be > 0: 0 is a startup validation error, never "unlimited") per owner per UTC day, table `daily_spend` in the
  SQLite DB (created with CREATE IF NOT EXISTS). Code `spend_cap_exceeded`. Reservation happens in the handler after body/doc/session validation (so 422/404 hold nothing) and before any LLM call or stream. Each run reserves its max USD
  (`AGENT_MAX_USD` / `SUMMARY_MAX_USD`) in memory; a request is refused when spent + in-flight reservations >= cap, so
  concurrent requests cannot all slip past; overshoot is at most one run's max. The reservation is reconciled to the
  actual `Budget.usd` when the run ends, including client disconnect, cancel, budget stop and errors. Leaked
  reservations expire after 15 min. A run straddling UTC midnight is billed to the day it settles. Settling is a small
  synchronous SQLite write, kept sync on purpose so it still completes during cancellation.
- **Global ceilings** (X-User-Id is trust-the-header, so invented ids multiply per-user quotas): `GLOBAL_RATE_LIMIT_PER_MIN`
  (default 120, 429 `global_rate_limited`) and `GLOBAL_DAILY_USD_CAP` (default 5.0, 429 `global_spend_cap_exceeded`),
  reserved/settled under the fixed key `*` (not a valid user id). When the 10 000-user limiter table is full of active
  users, NEW ids are refused (429 `rate_limited`) instead of evicting old entries, so an id flood cannot reset a victim's window.
- **Sessions**: `SESSION_MAX_PER_OWNER` (default 100) live sessions per owner, 429 `session_limit_exceeded`
  (count + insert in one transaction).
- **Logging**: log calls carry ids, sizes, timings, tokens, USD and exception class names only. Never document text,
  questions, answers, prompts, filenames or tool args; no `log.exception` (tracebacks carry message text); HTTP error
  details are logged only for our own fixed-string `ApiError`. Enforced by `tests/test_logging.py` (sentinel strings in
  document, filename, question and LLM error text never reach captured logs) and an AST scan of `src` for f-string/
  sensitive-name log calls.

**GATE B / PRODUCTION NOTE.** `X-User-Id` is trust-the-header, so per-user limits are only as strong as authentication:
do not expose this service publicly without real auth. Limiter and ledger reservations are per-process (a multi-worker
deploy needs a shared store such as Redis). Failed LLM calls that return no usage are uncharged. Set a provider-side hard
budget as the last backstop. Per-IP limiting is a backlog item (only meaningful behind a trusted proxy).

## Backend hardening from the web audit (T-049)

- **Upload limits (H1)**: `POST /v1/documents` is limited per `tenant:user` to `UPLOAD_RATE_PER_HOUR` (default 5) requests/hour, 429 `rate_limited` + `Retry-After`. Per-owner document cap: `anon-` owners `ANON_MAX_DOCS` (5), other owners `OWNER_MAX_DOCS` (200, 0 = off); demo tenant total stored bytes `DEMO_TENANT_MAX_BYTES` (200 MB). Rejections are per-file codes `doc_limit_reached` / `storage_limit_reached` (HTTP 200 body, ADR-0002); re-uploading bytes the owner already has is exempt. Caps are checked before ingest and are not transactional, so concurrent uploads can overshoot by a few files. `INGEST_MAX_CONCURRENT` (2): an app-wide semaphore; extra uploads wait (chosen over 429 so a burst within the rate limit still completes; the rate limit bounds the queue).
- **Mint limits (M1)**: `POST /v1/auth/anonymous` has a per-IP bucket and a global `ANON_MINT_GLOBAL_PER_HOUR` (200) bucket (429 `global_rate_limited`), checked after the per-IP one so IP-limited requests do not drain it. See docs/deploy.md for `TRUSTED_PROXY`.
- **No class names to clients (M3)**: unknown ingest exceptions are stored and returned as `ingest_failed`; the class is logged only. Legacy stored values outside the fixed set are mapped to `ingest_failed` on read.
- **Headers (L1)**: `X-Content-Type-Options: nosniff` and `Cache-Control: no-store` on every `/v1` response (pure ASGI middleware, streaming untouched; a route-set `Cache-Control`, e.g. SSE `no-cache`, is kept).
- **Body size (L2)**: `json_body_limit` (64 KB, Content-Length based) also on compare, document summary and sentiment.
- `/ping-checkpoint` removed.

## Known gaps

- Homoglyph role words (Cyrillic `\u0455ystem:`) are not folded by NFKC and are not rewritten; they stay inert DATA
  inside delimiters (documented by a test).

- Only the mock LLM is tested: a real model may still be persuaded to change its answer text (not its tool powers).
  Impact is bounded by the layers in 3 and by the groundedness check.
- Plain-text social engineering inside an answer ("email your password to ...") is not detectable by regex.
- Defanging removes legitimate links from documents; users lose clickable URLs in answers (accepted).

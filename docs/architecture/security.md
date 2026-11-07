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

## Known gaps

- Homoglyph role words (Cyrillic `\u0455ystem:`) are not folded by NFKC and are not rewritten; they stay inert DATA
  inside delimiters (documented by a test).

- Only the mock LLM is tested: a real model may still be persuaded to change its answer text (not its tool powers).
  Impact is bounded by the layers in 3 and by the groundedness check.
- Plain-text social engineering inside an answer ("email your password to ...") is not detectable by regex.
- Defanging removes legitimate links from documents; users lose clickable URLs in answers (accepted).

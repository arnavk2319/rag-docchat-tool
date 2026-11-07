# Summarization (T-012)

`api/summarize.py` loads each document's parent texts (document order, owner-scoped) and calls
`services/summarization.summarize_documents()`. No text cap: the whole document is covered.

Flow: pack sections into batches (`SUMMARY_BATCH_CHARS`, default 8000; at most `SUMMARY_MAX_BATCHES`, default 40,
else `partial: true`) -> optional compression per batch (`SUMMARY_COMPRESS`, default off) -> map call per batch
(`fast`, prompt `summarize_map`) -> reduce (`summarize_reduce`, role `SUMMARY_REDUCE_ROLE`, default `fast`). One
batch skips reduce. If partials exceed one batch, they are reduced in groups first (hierarchical collapse).

Cost guard: one `llm.Budget(max_usd=SUMMARY_MAX_USD)` (default 0.02) per summary. If the cap trips after at least one
map call, the joined partials are returned with `partial: true` (HTTP 200). If the first call trips it or the
provider fails before any output, the error propagates (429 / 502). The call that crosses the cap is paid but its
text is dropped (wrapper charges after the call).

Cache: in-process LRU (128) keyed by sha256(document names + section texts, prompt versions, reduce role, compress
flag, batch size). Hit = 0 LLM calls, `cached=true` internally. Partial results are never cached. Lost on restart.

Compression (`services/compression.py`, replaces the legacy `utilities/contextual_compressor.py`): LLM via
`llm.complete` (`fast`, prompt `compress`, plain text); empty output, `NONE`, output longer than input or a
non-budget LLM error falls back to `extractive_compress` (top-n sentences by word frequency plus focus overlap,
original order; first 300 chars when no sentence boundary). Own sha256 cache: a hit makes no call and returns one
entry per passage. `BudgetExceeded` is re-raised so the summary becomes partial.

Safety: document names and text are escaped (`<`, `>`) inside tagged blocks; prompts state the text is DATA.
Env: SUMMARY_MAX_USD, SUMMARY_REDUCE_ROLE, SUMMARY_COMPRESS, SUMMARY_BATCH_CHARS, SUMMARY_MAX_BATCHES.

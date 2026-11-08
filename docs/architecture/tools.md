# Agent tools (`services/agent/tools.py`)

Allow-list registry `TOOLS` (name -> Pydantic args model + function). `run_tool(name, raw_args, ctx)` never raises: unknown tool, invalid args or an internal failure all return an error result.

- `search_documents(query, doc_ids?, top_k<=8)`: hybrid retrieval -> parents -> cross-encoder rerank (`services/retrieval.py`, `rerank.py`); abstains ("no results") below `min_rerank_score`.
- `list_documents()`: the caller's documents (max 50).
- `get_page_or_section(doc_id, page?, section?)`: parent text for a page or section of a ready document.
- `summarize_document(doc_id)`: delegates to `services/summarization.summarize_documents` (content cache -> 0 LLM calls on repeat; `SUMMARY_MAX_USD` cap, partial flagged).
- `compare_documents(doc_ids 2-5, focus?)`: per doc hybrid retrieval + rerank (top 3 parents), then ONE `fast` call with `prompts/compare.md`; output ends with `Sources: [doc_id=..]` for every doc. More than 5 or fewer than 2 ids -> invalid arguments; duplicate, foreign or missing ids -> "not found".

Scope: `ToolContext(owner_id, doc_ids, registry, vectors, parents, bm25, embed, reranker)`. A doc id outside the owner or the allowed `doc_ids` returns the same "not found" as a missing id.

Untrusted output: every result is escaped (`< > &`), capped at 6000 chars and wrapped in `<tool_result status="ok|error">...</tool_result>`, so document text cannot forge a delimiter. Tools use services only (no direct store calls). Async services are called from the sync tools via `_run_async`. The agent loop consumes this.

## query_table (T-015)
`query_table(doc_id, sheet?, select?, filters[], group_by[], aggs[], sort[], limit<=50)` over csv/xlsx documents. Logic in `services/agent/table_query.py`: the op is a closed Pydantic model (filter ops `eq ne gt ge lt le contains in`; agg fns `sum mean min max count nunique`), translated to pandas masks/groupby. No eval/exec/`DataFrame.query`/expression strings. Column names must match `^[\w .\-/()%#]{1,64}$` and exist in the file. Caps: 50 rows out, 80 chars per cell, 5 MB file, 100k rows read, 200 columns, 10 filters. Non-csv/xlsx doc -> "not a table document"; foreign/missing/out-of-scope id -> same "not found". Raw bytes come from `ToolContext.table_loader(owner_id, doc_id)` (original uploads are not persisted yet; wiring a file store is a follow-up, without a loader the tool returns "not found").

# Agent tools (`services/agent/tools.py`)

Allow-list registry `TOOLS` (name -> Pydantic args model + function). `run_tool(name, raw_args, ctx)` never raises: unknown tool, invalid args or an internal failure all return an error result.

- `search_documents(query, doc_ids?, top_k<=8)`: hybrid retrieval -> parents -> cross-encoder rerank (`services/retrieval.py`, `rerank.py`); abstains ("no results") below `min_rerank_score`.
- `list_documents()`: the caller's documents (max 50).
- `get_page_or_section(doc_id, page?, section?)`: parent text for a page or section of a ready document.
- `summarize_document(doc_id)`: delegates to `services/summarization.summarize_documents` (content cache -> 0 LLM calls on repeat; `SUMMARY_MAX_USD` cap, partial flagged).
- `compare_documents(doc_ids 2-5, focus?)`: per doc hybrid retrieval + rerank (top 3 parents), then ONE `fast` call with `prompts/compare.md`; output ends with `Sources: [doc_id=..]` for every doc. More than 5 or fewer than 2 ids -> invalid arguments; duplicate, foreign or missing ids -> "not found".

Scope: `ToolContext(owner_id, doc_ids, registry, vectors, parents, bm25, embed, reranker)`. A doc id outside the owner or the allowed `doc_ids` returns the same "not found" as a missing id.

Untrusted output: every result is escaped (`< > &`), capped at 6000 chars and wrapped in `<tool_result status="ok|error">...</tool_result>`, so document text cannot forge a delimiter. Tools use services only (no direct store calls). Async services are called from the sync tools via `_run_async`. The agent loop consumes this.

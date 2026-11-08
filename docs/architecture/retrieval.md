# Retrieval (T-010)

- Ingest: after children are embedded into Chroma, `Bm25Store.add_doc` tokenizes (lowercase `\w+`) and writes `<CHROMA_DIR>_bm25/<hash(owner)>-<hash(doc)>.json` (owner_id, doc_id, chunk_ids, tokens). JSON only, no pickle; atomic tmp+replace.
- IDF (T-023): the BM25 index is owner-wide, so IDF is shared across all of an owner's documents; `doc_ids` scoping filters scores but their IDF still includes out-of-scope documents of the same owner (comparable scores across documents; slight corpus-dependence).
- Startup: `Bm25Store.__init__` calls `load_all`, loading token lists (the owner-wide `BM25Okapi` is built lazily on first query). Corrupt files are skipped.
- Delete/purge/failed ingest: `IngestionService` removes the doc's BM25 file and memory entry alongside vectors and parents.
- Query: `services/retrieval.hybrid_search` takes dense top 20 (Chroma, `doc_ids` `$in` plus mandatory owner filter) and BM25 top 20 (only chunks sharing a query token), fuses with RRF (`1/(60+rank)`), returns the top_k Hits. BM25-only chunks are fetched with `ChromaStore.get` (owner-filtered).
- Scope: `/v1/query` passes the caller's resolved ready doc ids; an empty list matches nothing.
- Rerank (T-011): `/v1/query` fetches max(top_k, 20) fused children, `expand_to_parents` dedupes to parents, `services/rerank.rerank_parents` scores (question, parent text) pairs with the registry reranker (`bge-reranker-base`, `FakeReranker` lexical overlap in tests) and keeps the top `top_k` (default 5). Rerank ignores child distance, so BM25-only hits (placeholder distance 1.0) are scored on text.
- Abstention: if the best rerank score < `MIN_RERANK_SCORE` (default 0.0 = raw cross-encoder logit; tune with evals) the response is "I don't know." with no citations and zero LLM calls.
- Citations: one per surviving parent, on its best child chunk: doc_id, filename, chunk_id, page|sheet, span (char offsets into the page text). Prompt: `prompts/answer.md` v1.

# Retrieval (T-010)

- Ingest: after children are embedded into Chroma, `Bm25Store.add_doc` tokenizes (lowercase `\w+`) and writes `<CHROMA_DIR>_bm25/<hash(owner)>-<hash(doc)>.json` (owner_id, doc_id, chunk_ids, tokens). JSON only, no pickle; atomic tmp+replace.
- Startup: `Bm25Store.__init__` calls `load_all`, rebuilding in-memory `BM25Okapi` per doc from tokens. Corrupt files are skipped.
- Delete/purge/failed ingest: `IngestionService` removes the doc's BM25 file and memory entry alongside vectors and parents.
- Query: `services/retrieval.hybrid_search` takes dense top 20 (Chroma, `doc_ids` `$in` plus mandatory owner filter) and BM25 top 20 (only chunks sharing a query token), fuses with RRF (`1/(60+rank)`), returns the top_k Hits. BM25-only chunks are fetched with `ChromaStore.get` (owner-filtered).
- Scope: `/v1/query` passes the caller's resolved ready doc ids; an empty list matches nothing.
- Next: rerank and score abstention (later tickets).

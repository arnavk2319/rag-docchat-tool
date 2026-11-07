# ADR-0001 Stack and architecture
Status: proposed

## Context
Prototype (src/backend/app) is a LangChain + HTTP Chroma + OpenAI app with a bloated requirements.txt and blocking model loads. Spec (docs/spec.md) wants local-first, cheap, cited, bounded-agent RAG. This ADR fixes the stack; nothing outside it is added without a new ADR.

## Decision
Format: choice | rationale | cost / risk / reversibility.

1. Framework: FastAPI + Pydantic v2 + pydantic-settings, Python 3.12, uv | stack overlay, async + SSE native | free / low / easy.
2. Registry: stdlib `sqlite3` (WAL) in a threadpool, plain SQL, no ORM | single process, no server, no new dep | free / low; swap to Postgres later behind `store/registry.py` interface.
3. Vector store: embedded Chroma `PersistentClient(CHROMA_DIR)`, cosine, metadata `doc_id`/`owner_id` filters | no server to run, already used in prototype | free / single-writer only, one process; reversible via `VectorStore` protocol.
4. Lexical: `rank-bm25` (BM25Okapi), per-doc index built at ingest, persisted with pickle-free JSON tokens and rebuilt on startup | tiny, pure Python | free / O(n) memory, fine under ~10^5 chunks; replaceable.
5. Embeddings: `sentence-transformers` `all-MiniLM-L6-v2` (local); rerank `BAAI/bge-reranker-base` via `CrossEncoder` (local) | zero API cost | ~2 GB RAM, cold start; loaded once in lifespan registry; ids in config.
6. LLM provider: hosted Claude via `anthropic` SDK default (`AGENT_MODEL=claude-sonnet-5-5`, `FAST_MODEL=claude-haiku-5-5`); Ollama optional via `httpx` to its REST API (no extra SDK) | best tool-calling, pay per use | spend capped (section 7); Ollama best-effort for agent; provider switchable by env.
7. `llm` wrapper (`core/llm.py`), the only module that imports `anthropic`/calls Ollama:
   - `async complete(messages, *, model_role, schema=None, max_tokens, timeout)`; roles `agent|fast` map to config ids, never in call sites.
   - Timeout via httpx/SDK timeout (default 30 s); retry 3x on 429/5xx/timeouts with exponential backoff + jitter (tenacity-free, ~20 lines); no retry on 4xx.
   - Per call log (ids, role, model, in/out tokens, USD from a price table in config, latency) never the prompt or text; accumulate into a per-run `Budget` that raises `BudgetExceeded` on steps/tokens/USD; per-user daily spend tracked in SQLite.
   - Structured output: Pydantic model validation of JSON reply (tool-use forced schema for Claude, `format` for Ollama); one repair retry, then `LLMValidationError`.
   - Mock mode (`LLM_PROVIDER=mock`): deterministic, hash-of-prompt keyed canned responses plus scripted queues for agent tests; zero network. Tests set it by default and a conftest guard blocks real sockets to the API host.
8. RAG: parent-child chunks (child ~300 tokens embedded, parent ~1200 tokens returned for context; page/sheet/section and char offsets kept) -> hybrid BM25 + dense (top 20 each, Reciprocal Rank Fusion) -> cross-encoder rerank to top 5 parents -> optional Haiku compression. Citations = `doc_id, filename, chunk_id, page|sheet, span` returned with every answer. Abstain with "I don't know" when top rerank score < `MIN_RERANK_SCORE` (calibrated by eval). Summarization: map (Haiku per section batch) -> reduce (Sonnet or Haiku, config), cached by `sha256` | fixed-cost, eval-tunable | cheap; thresholds reversible via config.
9. Agent: LangGraph `StateGraph` (spec P3), used only for the agent; retrieval/ingestion are plain functions (drop `langchain`, `langchain_community`, `llama-index`). Nodes: router (direct answer vs retrieve) -> plan -> tool -> grade (relevance, Haiku) -> rewrite (max 2 retries) -> answer (with citations) -> groundedness check (fail -> one regenerate or abstain). Tools allow-listed, read-only, scoped to caller `doc_id`s: `search_documents, summarize_document, get_page_or_section, list_documents, query_table (pandas, no exec/eval), compare_documents`. Bounds: `AGENT_MAX_STEPS` (8), tokens, `AGENT_MAX_USD` (0.05), enforced in graph via `recursion_limit` and `Budget`. Tool output wrapped in delimiters and treated as data. SSE (`text/event-stream`) streams step, tool, token and final-citations events. Rationale: LangGraph gives checkpointing/streaming and matches spec; cost = one dep. Risk: API churn; mitigated by thin `agent/graph.py` and node functions that are plain async callables, so a hand-rolled loop is a drop-in fallback.
10. Layout (`src/backend/app`): `main.py` (lifespan, registry); `api/` (routers: upload, query, summarize, agent, documents; SSE; deps for auth/rate limit); `services/` (ingestion, retrieval, summarization, agent/ with graph, nodes, tools); `core/` (config, llm, models registry, budget, logging, security); `store/` (chroma, registry, bm25); `prompts/` at repo root as `prompts/*.md` with version header. Tools call services only. Old `routers/`, `utilities/` are migrated then removed.
11. Hosting: local-first, `uv run uvicorn`, single process, no Docker yet (Later). Header-based user id until real auth.

### Pinned dependencies
Runtime. Versions marked (V) are my best recollection; run `uv lock` and confirm at lock time, then pin exact.
- fastapi==0.115.6 (V), `uvicorn[standard]==0.34.0` (V), pydantic==2.10.4 (V), pydantic-settings==2.7.0 (V), python-multipart==0.0.20 (V)
- anthropic (verify at lock time; use latest 0.x supporting the models above), httpx==0.28.1 (V)
- langgraph (verify at lock time), chromadb (verify at lock time; 1.x line)
- sentence-transformers (verify at lock time), torch CPU (transitive; verify)
- rank-bm25==0.2.2
- pypdf (verify at lock time), python-docx==1.1.2 (V), openpyxl==3.1.5, pandas (verify at lock time), msoffcrypto-tool (verify at lock time, keep only if encrypted xlsx is in scope; else drop)
Dev: pytest (verify), pytest-asyncio (verify), ruff (verify), httpx (above), mypy optional (not added).

### Remove from old requirements.txt
`asyncio` (PyPI backport, breaks stdlib), `faiss-cpu` (Chroma is the store), `flagembedding` (use sentence-transformers CrossEncoder), `sqlalchemy`, `psycopg2-binary` (stdlib sqlite3), `logtail-python` (stdlib logging, JSON to stdout), `llama-index`, `langchain`, `langchain-core`, `langchain_openai`, `langchain_community`, `langchain-huggingface`, `langchain-text-splitters` (small in-house splitter; no OpenAI path), `openai`, `unstructured`, `docx2txt` (python-docx covers it), duplicate `python-docx`, `python-dotenv` (pydantic-settings reads .env), `transformers` and `scikit-learn` (not direct deps; transformers arrives transitively), `aiohttp` (httpx), `jinja2` (prompts are plain `.md` with `str.format`-style placeholders), `pydantic` unpinned line replaced by pinned entry.

## Alternatives
- Chroma HTTP server: extra process, no benefit single-node. Qdrant/pgvector/FAISS: more infra or no metadata filtering; revisit at multi-user scale.
- Hand-rolled agent loop: fewer deps, but loses streaming/state structure; kept as fallback.
- Hosted embeddings/rerankers (Voyage, Cohere): cost per call and a network dependency; local is free.
- SQLAlchemy/Postgres: overkill for single process.
- Sonnet for all steps: simpler, ~3-5x cost on sub-steps.
- OpenAI default: prototype key is exposed and spec chose Claude.

## Consequences (cost, risk, reversibility)
- Cost: embeddings/rerank/BM25 free; LLM spend bounded at $0.05/agent request, $0.02/summary, plus daily cap; test runs $0.
- Risk: single-writer Chroma and SQLite limit scale-out to one process; local models need ~2 GB RAM; version pins unverified until lock; Ollama tool-calling unreliable.
- Reversibility: store, registry, provider and agent runtime sit behind small interfaces (`VectorStore`, `Registry`, `llm`), so each is a one-ticket swap. Dependency removal is one-way but cheap to re-add via ADR.

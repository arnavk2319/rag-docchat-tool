# Architecture overview

Layers (imports point downward only): `api` -> `services` -> `core` -> `store`.

- `api`: FastAPI routers, request/response models, auth and rate limiting. No business logic.
- `services`: use cases (ingest, search, answer, summarize, agent).
- `core`: pure logic and adapters (chunking, retrieval, rerank, `llm` wrapper, config).
- `store`: persistence (SQLite registry, Chroma vector store, BM25 index).

Request flow: client -> `api` route (validate, auth, rate limit) -> `services` use case -> `core` (retrieve, rerank, `llm` call) -> `store` (Chroma/SQLite) -> answer with citations (source id + span) back up the stack.

Module map (under `src/backend/app/`): `main.py` app and lifespan; `config.py` settings; `api/`, `services/`, `core/`, `store/` as above. The legacy `routers/` and the langchain-based `utilities/reranker.py` and `contextual_compressor.py` were removed in T-007; `utilities/` keeps only `file_handler.py` (parsers) and `logging_module.py` until they move into `core/`. Query path adds `services/rerank.py` (cross-encoder rerank + `MIN_RERANK_SCORE` abstention in `api/query.py`). Prompts: `prompts/*.md` (loader `core/prompts.py`). Upload security (sniffing, size and zip limits) lives in `core/security.py`; the per-file upload pipeline in `api/uploads.py`.

Models (embedder, reranker) are loaded once at startup by the registry in `core/models.py`; see [models.md](models.md). Blocking work goes through `core/concurrency.run_blocking`.

Ingestion (`services/ingestion.py`) parses, chunks, embeds and stores documents in embedded Chroma (`store/chroma.py`) with `doc_id`/`owner_id` metadata; see [ingestion.md](ingestion.md).

HTTP API (`api/`): routers `documents`, `summarize`, `query` under `/v1`, `deps.py` (temporary `X-User-Id` auth and app-wide services on `app.state`: registry, vector store, ingestion service, created in the lifespan), `errors.py` ({detail, code} bodies), `schemas.py`. See [api.md](api.md) and ADR-0002.

Agent tools (`services/agent/tools.py`, allow-listed, owner-scoped, escaped results): see [tools.md](tools.md).

# Architecture overview

Layers (imports point downward only): `api` -> `services` -> `core` -> `store`.

- `api`: FastAPI routers, request/response models, auth and rate limiting. No business logic.
- `services`: use cases (ingest, search, answer, summarize, agent).
- `core`: pure logic and adapters (chunking, retrieval, rerank, `llm` wrapper, config).
- `store`: persistence (SQLite registry, Chroma vector store, BM25 index).

Request flow: client -> `api` route (validate, auth, rate limit) -> `services` use case -> `core` (retrieve, rerank, `llm` call) -> `store` (Chroma/SQLite) -> answer with citations (source id + span) back up the stack.

Module map (under `src/backend/app/`): `main.py` app and lifespan; `config.py` settings; `api/`, `services/`, `core/`, `store/` as above. `routers/` and `utilities/` are the legacy prototype, replaced by T-003 onward. Prompts: `prompts/*.md`.

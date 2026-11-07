# Model registry (`core/models.py`)

- Holds the embedder (`EMBED_MODEL`, default `sentence-transformers/all-MiniLM-L6-v2`) and reranker (`RERANK_MODEL`, default `BAAI/bge-reranker-base`). Ids come from `core/config.py`, never from call sites.
- `load_models(settings)` is called once in the FastAPI lifespan (in a worker thread via `run_blocking`) and caches a `ModelRegistry`; later calls return the same object. `get_models()` returns it, `models_loaded()` feeds `GET /healthz` (`{"models_loaded": true}`).
- `MODELS_FAKE=1` swaps in deterministic `FakeEmbedder`/`FakeReranker`; set in `tests/conftest.py` so tests never download weights. Tests call `reset_models()` between cases.
- `core/concurrency.py: run_blocking(fn, *args)` runs blocking work (encoding, rerank, parsing) on the Starlette threadpool so the event loop stays responsive.
- Parsers (`utilities/file_handler.py`) raise `ParseError`; docx/xlsx are stream-decompressed against `MAX_DECOMPRESSED_BYTES` (actual bytes, not declared sizes) and raise `DecompressionLimitError`.
- Text and CSV are decoded as `utf-8-sig` (BOM stripped); other encodings such as latin-1 are unsupported by design and raise `ParseError`.

## Known limits
- MODELS_FAKE is honoured only under pytest; elsewhere startup raises RuntimeError. `/healthz` reports `fake` so a fake registry is visible.
- CSV is read fully into memory (bounded by the 10 MB upload cap).
- docx/xlsx may be decompressed up to the 200 MB cap per request in a worker thread (CPU cost; revisit in the perf phase).

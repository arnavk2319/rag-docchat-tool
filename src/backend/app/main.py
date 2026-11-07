import tracemalloc
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.exceptions import HTTPException

from .api import documents, query, summarize
from .api.errors import http_error, install_error_handlers
from .core.concurrency import run_blocking
from .core.config import get_settings
from .core.llm import validate_provider
from .core.models import load_models, models_fake, models_loaded, reset_models
from .services.ingestion import IngestionService
from .store.bm25 import Bm25Store
from .store.chroma import ChromaStore
from .store.parents import SqliteParents
from .store.registry import SqliteRegistry
from .utilities.logging_module import logger

tracemalloc.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_provider()
    logger.info("Starting the RAG doc chat tool")
    settings = get_settings()
    await run_blocking(load_models, settings)
    # App-wide services; tests may pre-set any of these on app.state (or override the deps).
    state = app.state
    created = []
    if getattr(state, "registry", None) is None:
        state.registry = await run_blocking(SqliteRegistry, settings.db_path)
        created.append("registry")
    if getattr(state, "store", None) is None:
        state.store = await run_blocking(ChromaStore, settings.chroma_dir)
        created.append("store")
    if getattr(state, "parents", None) is None:
        state.parents = await run_blocking(SqliteParents, settings.db_path)
        created.append("parents")
    if getattr(state, "bm25", None) is None:
        # Rebuilt from the persisted JSON token files at startup.
        state.bm25 = await run_blocking(Bm25Store, f"{settings.chroma_dir}_bm25")
        created.append("bm25")
    if getattr(state, "ingestion", None) is None:
        state.ingestion = IngestionService(
            state.registry, state.store, parents=state.parents, bm25=state.bm25
        )
        created.append("ingestion")
    try:
        yield
    finally:
        for name in created:
            setattr(state, name, None)
        reset_models()
    logger.info("App now shutting down")


app = FastAPI(lifespan=lifespan)
app.include_router(documents.router)
app.include_router(summarize.router)
app.include_router(query.router)


async def http_exception_handle_logging(request, exc: HTTPException):
    logger.error("HTTPException: %s %s", exc.status_code, exc.detail)
    return http_error(exc)


install_error_handlers(app, http_exception_handle_logging)


@app.get("/healthz", tags=["health"])
def healthz():
    return {"models_loaded": models_loaded(), "fake": models_fake()}


@app.get("/ping-checkpoint", tags=["health"])
def ping_checkpoint():
    logger.info("Checking endpoint status")
    return "Returning OK 200"

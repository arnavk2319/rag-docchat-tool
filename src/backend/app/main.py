import tracemalloc
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.exceptions import HTTPException

from .api import documents, query, sessions, summarize
from .api.deps import ApiError
from .api.errors import http_error, install_error_handlers
from .core.concurrency import run_blocking
from .core.config import get_settings
from .core.limits import RateLimiter, SpendLedger
from .core.llm import validate_provider
from .core.models import load_models, models_fake, models_loaded, reset_models
from .objectstore import build_object_store
from .services.ingestion import IngestionService
from .store.bm25 import Bm25Store
from .store.parents import SqliteParents
from .store.pg import PostgresRegistry, PostgresSessions
from .store.registry import InMemoryRegistry
from .store.sessions import InMemorySessions
from .store.spend import SqliteSpend
from .utilities.logging_module import logger
from .vectorstore import InMemoryVectorStore, PgVectorStore, SyncVectors

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
    pools: list = []
    db_url = settings.database_url.get_secret_value()
    sync_pool = None
    if db_url:  # Postgres mode: schema is applied beforehand by `python -m app.db.migrate` (local only)
        from psycopg_pool import AsyncConnectionPool, ConnectionPool

        sync_pool = ConnectionPool(db_url, min_size=1, max_size=settings.db_pool_max, open=False)
        await run_blocking(sync_pool.open)
        pools.append(sync_pool)
    else:
        logger.warning("DATABASE_URL not set: registry, sessions and vectors are in-memory (dev only)")
    if getattr(state, "registry", None) is None:
        state.registry = PostgresRegistry(sync_pool) if sync_pool else InMemoryRegistry()
        created.append("registry")
    if getattr(state, "store", None) is None:
        if db_url:
            apool = AsyncConnectionPool(db_url, min_size=1, max_size=settings.db_pool_max, open=False)
            await apool.open()
            pools.append(apool)
            state.store = SyncVectors(PgVectorStore(apool))
        else:
            state.store = SyncVectors(InMemoryVectorStore())
        created.append("store")
    if getattr(state, "parents", None) is None:
        state.parents = await run_blocking(SqliteParents, settings.db_path)
        created.append("parents")
    if getattr(state, "bm25", None) is None:
        state.bm25 = Bm25Store()  # rebuilt lazily from the stored chunk text on first query
        created.append("bm25")
    if getattr(state, "sessions", None) is None:
        sess_kw = {
            "max_messages": settings.session_max_messages,
            "max_tokens": settings.session_max_tokens,
            "ttl_hours": settings.session_ttl_hours,
        }
        state.sessions = await run_blocking(
            lambda: PostgresSessions(sync_pool, **sess_kw) if sync_pool else InMemorySessions(**sess_kw)
        )  # Postgres variant also purges expired sessions at startup
        created.append("sessions")
    if getattr(state, "limiter", None) is None:
        state.limiter = RateLimiter(settings.rate_limit_per_min)
        created.append("limiter")
    if getattr(state, "global_limiter", None) is None:
        state.global_limiter = RateLimiter(settings.global_rate_limit_per_min, max_users=1)
        created.append("global_limiter")
    if getattr(state, "ledger", None) is None:
        state.ledger = await run_blocking(
            lambda: SpendLedger(
                SqliteSpend(settings.db_path), settings.daily_usd_cap,
                global_cap_usd=settings.global_daily_usd_cap,
            )
        )
        created.append("ledger")
    if getattr(state, "objects", None) is None:
        state.objects = build_object_store(settings)
        created.append("objects")
    if getattr(state, "ingestion", None) is None:
        state.ingestion = IngestionService(
            state.registry, state.store.inner, parents=state.parents, bm25=state.bm25,
            objects=state.objects,
        )
        created.append("ingestion")
    try:
        yield
    finally:
        for name in created:
            setattr(state, name, None)
        reset_models()
        for pool in pools:
            if hasattr(pool, "__aenter__"):
                await pool.close()
            else:
                await run_blocking(pool.close)
    logger.info("App now shutting down")


app = FastAPI(lifespan=lifespan)
app.include_router(documents.router)
app.include_router(summarize.router)
app.include_router(query.router)
app.include_router(sessions.router)


async def http_exception_handle_logging(request, exc: HTTPException):
    # Detail only for our own ApiError (fixed strings); framework/other details may echo input.
    detail = exc.detail if isinstance(exc, ApiError) else "-"
    logger.error("HTTPException: %s %s", exc.status_code, detail)
    return http_error(exc)


install_error_handlers(app, http_exception_handle_logging)


@app.get("/healthz", tags=["health"])
def healthz():
    return {"models_loaded": models_loaded(), "fake": models_fake()}


@app.get("/ping-checkpoint", tags=["health"])
def ping_checkpoint():
    logger.info("Checking endpoint status")
    return "Returning OK 200"

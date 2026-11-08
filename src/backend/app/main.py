import tracemalloc
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException

from .api import auth_anon, compare, docintel, documents, query, sessions, summarize
from .api.deps import ApiError, demo_caps
from .api.errors import http_error, install_error_handlers
from .core.concurrency import run_blocking
from .core.config import cors_origins, get_settings
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
                tenant_cap_usd=settings.tenant_daily_usd_cap, tenant_caps=demo_caps(settings),
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


_DESCRIPTION = (
    "DocChat RAG API. All business routes live under /v1 and need `Authorization: Bearer <token>`. "
    "Errors are `{detail, code}`. `POST /v1/query` with `stream=true` replies as Server-Sent Events "
    "(`text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`)."
)


async def http_exception_handle_logging(request, exc: HTTPException):
    # Detail only for our own ApiError (fixed strings); framework/other details may echo input.
    detail = exc.detail if isinstance(exc, ApiError) else "-"
    logger.error("HTTPException: %s %s", exc.status_code, detail)
    return http_error(exc)


def create_app() -> FastAPI:
    settings = get_settings()
    docs = settings.enable_docs
    application = FastAPI(
        title="DocChat RAG", version="1", description=_DESCRIPTION, lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    origins = cors_origins(settings)
    if origins:  # bearer tokens, not cookies: no credentials, so an allow-list is safe
        application.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"], max_age=600,
            expose_headers=["Retry-After"],
        )
    application.include_router(documents.router)
    application.include_router(summarize.router)
    application.include_router(docintel.router)
    application.include_router(compare.router)
    application.include_router(query.router)
    application.include_router(sessions.router)
    application.include_router(auth_anon.router)
    install_error_handlers(application, http_exception_handle_logging)

    @application.get("/health", tags=["health"])
    def health():
        return {"status": "ok"}  # liveness only: no DB, no LLM, no auth

    @application.get("/healthz", tags=["health"], include_in_schema=False)
    def healthz():
        if not get_settings().enable_docs:  # H-5: no internals in prod
            return {"status": "ok"}
        return {"models_loaded": models_loaded(), "fake": models_fake()}

    @application.get("/ping-checkpoint", tags=["health"], include_in_schema=False)
    def ping_checkpoint():
        logger.info("Checking endpoint status")
        return "Returning OK 200"

    return application


app = create_app()

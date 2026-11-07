import tracemalloc
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler

from .core.concurrency import run_blocking
from .core.config import get_settings
from .core.llm import validate_provider
from .core.models import load_models, models_fake, models_loaded, reset_models
from .routers.response import router as upload_router
from .utilities.logging_module import logger

tracemalloc.start()

@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_provider()
    logger.info("Starting the RAG doc chat tool")
    await run_blocking(load_models, get_settings())
    try:
        yield
    finally:
        reset_models()
    logger.info("App now shutting down")

app = FastAPI(lifespan=lifespan)
app.include_router(upload_router)

@app.exception_handler(HTTPException)
async def http_exception_handle_logging(request, exc):
    logger.error("HTTPException: %s %s", exc.status_code, exc.detail)
    return await http_exception_handler(request, exc)


@app.get("/healthz", tags=["health"])
def healthz():
    return {"models_loaded": models_loaded(), "fake": models_fake()}

import tracemalloc
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler

from .utilities.logging_module import logger

tracemalloc.start()

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting the RAG doc chat tool")
    yield
    logger.info("App now shutting down")

app = FastAPI(lifespan=lifespan)

@app.exception_handler(HTTPException)
async def http_exception_handle_logging(request, exc):
    logger.error(f"HTTPException: ${exc.status_code} ${exc.detail}")
    return await http_exception_handler(request, exc)

# Prototype router disabled until T-003/T-004 replace its langchain-based code.
import logging
import tracemalloc
from fastapi import FastAPI, APIRouter, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler

from contextlib import asynccontextmanager
from .routers.response import router
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

app.include_router(router)
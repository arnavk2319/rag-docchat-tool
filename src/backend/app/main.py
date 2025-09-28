import logging
from fastapi import FastAPI, APIRouter, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler

from .routers.response import router

app = FastAPI()
logger = logging.getLogger(__name__)

@app.exception_handler(HTTPException)
async def http_exception_handle_logging(request, exc):
    logger.error(f"HTTPException: ${exc.status_code} ${exc.detail}")
    return await http_exception_handler(request, exc)

app.include_router(router)
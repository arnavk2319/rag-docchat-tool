"""Error normalisation: every error body is {detail, code} (ADR-0002 section 6)."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ..core.llm import BudgetExceeded, LLMTransportError, LLMValidationError
from ..services.ingestion import DocumentBusyError
from .deps import ApiError

log = logging.getLogger(__name__)

_STATUS_CODES = {
    400: "bad_request",
    401: "auth_required",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    422: "validation_error",
    429: "rate_limited",
}


def error_response(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code}, status_code=status)


def http_error(exc: HTTPException) -> JSONResponse:
    code = exc.code if isinstance(exc, ApiError) else _STATUS_CODES.get(exc.status_code, "error")
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
    resp = error_response(exc.status_code, code, detail)
    if exc.headers:
        resp.headers.update(exc.headers)
    return resp


async def _http(_: Request, exc: HTTPException) -> JSONResponse:
    return http_error(exc)


async def _validation(_: Request, __: RequestValidationError) -> JSONResponse:
    # input values are never echoed back
    return error_response(422, "validation_error", "Request validation failed")


async def _budget(_: Request, __: BudgetExceeded) -> JSONResponse:
    return error_response(429, "budget_exceeded", "LLM budget exceeded")


async def _transport(_: Request, exc: LLMTransportError) -> JSONResponse:
    log.warning("llm transport error status=%s", exc.status)
    return error_response(502, "llm_unavailable", "The language model is unavailable")


async def _bad_output(_: Request, __: LLMValidationError) -> JSONResponse:
    return error_response(502, "llm_bad_output", "The language model returned invalid output")


async def _busy(_: Request, __: DocumentBusyError) -> JSONResponse:
    return error_response(409, "document_busy", "Document is being processed; retry later")


async def _internal(_: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled error %s", type(exc).__name__)
    return error_response(500, "internal", "Internal server error")


def install_error_handlers(app: FastAPI, http_handler=_http) -> None:
    app.add_exception_handler(HTTPException, http_handler)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(BudgetExceeded, _budget)
    app.add_exception_handler(LLMTransportError, _transport)
    app.add_exception_handler(LLMValidationError, _bad_output)
    app.add_exception_handler(DocumentBusyError, _busy)
    app.add_exception_handler(Exception, _internal)

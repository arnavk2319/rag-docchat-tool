"""FastAPI dependencies: temporary header auth and app-wide services (ADR-0002 section 1).

TEMPORARY auth: any caller can claim any user id. Replace `current_user` only (JWT/cookie
returning the same UserId); routers never read headers themselves. Tests use
`app.dependency_overrides[current_user]` and may override the service getters.
"""

import re
from typing import Annotated, NewType

from fastapi import Depends, Header, HTTPException, Request

from ..services.ingestion import IngestionService
from ..store.bm25 import Bm25Store
from ..store.chroma import VectorStore
from ..store.parents import SqliteParents
from ..store.registry import Registry

UserId = NewType("UserId", str)

MAX_USER_ID_LEN = 64
_USER_ID = re.compile(r"[A-Za-z0-9_.@-]+")


class ApiError(HTTPException):
    """HTTPException carrying a machine-readable `code` for the {detail, code} body."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


def current_user(x_user_id: Annotated[str | None, Header()] = None) -> UserId:
    if x_user_id is None or not x_user_id.strip():
        raise ApiError(401, "auth_required", "X-User-Id header is required")
    if len(x_user_id) > MAX_USER_ID_LEN or not _USER_ID.fullmatch(x_user_id):
        raise ApiError(401, "invalid_user_id", "X-User-Id is invalid")
    return UserId(x_user_id)


def get_registry(request: Request) -> Registry:
    return request.app.state.registry


def get_store(request: Request) -> VectorStore:
    return request.app.state.store


def get_parents(request: Request) -> SqliteParents:
    return request.app.state.parents


def get_bm25(request: Request) -> Bm25Store | None:
    return getattr(request.app.state, "bm25", None)


def get_ingestion(request: Request) -> IngestionService:
    return request.app.state.ingestion


CurrentUser = Annotated[UserId, Depends(current_user)]
RegistryDep = Annotated[Registry, Depends(get_registry)]
StoreDep = Annotated[VectorStore, Depends(get_store)]
ParentsDep = Annotated[SqliteParents, Depends(get_parents)]
Bm25Dep = Annotated[Bm25Store | None, Depends(get_bm25)]
IngestionDep = Annotated[IngestionService, Depends(get_ingestion)]


MAX_JSON_BYTES = 64 * 1024


def json_body_limit(request: Request) -> None:
    """JSON bodies are capped at 64 KB (Content-Length check)."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_JSON_BYTES:
        raise ApiError(413, "too_large", "Request body is too large")

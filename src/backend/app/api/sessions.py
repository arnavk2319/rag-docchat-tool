"""POST/GET/DELETE /v1/sessions: explicit, owner-scoped chat memory (ADR-0002 section 5)."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from ..core.concurrency import run_blocking
from ..core.config import get_settings
from ..core.security import sanitize_output, sanitize_untrusted
from ..store.sessions import SessionLimitError
from .deps import ApiError, CurrentUser, SessionsDep
from .schemas import (
    ErrorBody,
    SessionCreated,
    SessionList,
    SessionListItem,
    SessionMessageOut,
    SessionOut,
)

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])
_ERR = {401: {"model": ErrorBody}, 404: {"model": ErrorBody}}


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Session not found")  # same for unknown and foreign


@router.post("", status_code=201, response_model=SessionCreated, responses={401: {"model": ErrorBody}, 429: {"model": ErrorBody}})
async def create_session(user: CurrentUser, sessions: SessionsDep) -> SessionCreated:
    try:
        sid = await run_blocking(sessions.create, user, get_settings().session_max_per_owner)
    except SessionLimitError:
        raise ApiError(429, "session_limit_exceeded", "Too many sessions; delete one first") from None
    return SessionCreated(session_id=sid)


TITLE_CHARS = 80


def make_title(first_question: str) -> str:
    """Untrusted user text: collapse whitespace, then sanitize with an 80-char cap on the output."""
    return sanitize_untrusted(" ".join(first_question.split()), TITLE_CHARS) or "New chat"


@router.get("", response_model=SessionList, responses={401: {"model": ErrorBody}})
async def list_sessions(
    user: CurrentUser,
    sessions: SessionsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> SessionList:
    rows, total = await run_blocking(sessions.list_sessions, user, limit, offset)
    items = [
        SessionListItem(session_id=r.session_id, title=make_title(r.first_question),
                        updated_at=r.updated_at)
        for r in rows
    ]
    return SessionList(items=items, total=total)


@router.get("/{session_id}", response_model=SessionOut, responses=_ERR)
async def get_session(session_id: str, user: CurrentUser, sessions: SessionsDep) -> SessionOut:
    msgs = await run_blocking(sessions.messages, user, session_id)
    if msgs is None:
        raise not_found()
    return SessionOut(
        session_id=session_id,
        messages=[SessionMessageOut(**{**m.model_dump(), "content": sanitize_output(m.content)}) for m in msgs],
    )


@router.delete("/{session_id}", status_code=204, responses=_ERR)
async def delete_session(session_id: str, user: CurrentUser, sessions: SessionsDep) -> Response:
    if not await run_blocking(sessions.delete, user, session_id):
        raise not_found()
    return Response(status_code=204)

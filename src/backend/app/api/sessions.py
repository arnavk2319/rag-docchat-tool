"""POST/GET/DELETE /v1/sessions: explicit, owner-scoped chat memory (ADR-0002 section 5)."""

from fastapi import APIRouter, Response

from ..core.concurrency import run_blocking
from ..core.config import get_settings
from ..store.sessions import SessionLimitError
from .deps import ApiError, CurrentUser, SessionsDep
from .schemas import ErrorBody, SessionCreated, SessionMessageOut, SessionOut

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


@router.get("/{session_id}", response_model=SessionOut, responses=_ERR)
async def get_session(session_id: str, user: CurrentUser, sessions: SessionsDep) -> SessionOut:
    msgs = await run_blocking(sessions.messages, user, session_id)
    if msgs is None:
        raise not_found()
    return SessionOut(
        session_id=session_id,
        messages=[SessionMessageOut(**m.model_dump()) for m in msgs],
    )


@router.delete("/{session_id}", status_code=204, responses=_ERR)
async def delete_session(session_id: str, user: CurrentUser, sessions: SessionsDep) -> Response:
    if not await run_blocking(sessions.delete, user, session_id):
        raise not_found()
    return Response(status_code=204)

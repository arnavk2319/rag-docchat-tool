"""Document endpoints: upload (multi-file), list, get, delete."""

from typing import Annotated

from fastapi import APIRouter, File, Query, Request, Response, UploadFile

from ..core.concurrency import run_blocking
from ..store.registry import Document
from .deps import ApiError, CurrentUser, IngestionDep, RegistryDep
from .schemas import DocumentList, DocumentOut, ErrorBody, UploadResponse
from .uploads import RequestTooLarge, process_upload

router = APIRouter(prefix="/v1/documents", tags=["documents"])

MAX_FILES = 10
MAX_REQUEST_BYTES = 50 * 1024 * 1024
_VALIDATION_CODES = {"too_large", "bad_extension", "content_mismatch", "bad_content_type"}
_ERRORS = {401: {"model": ErrorBody}, 404: {"model": ErrorBody}}


def to_out(d: Document) -> DocumentOut:
    return DocumentOut(**d.model_dump(exclude={"owner_id"}))


@router.post(
    "",
    response_model=UploadResponse,
    responses={400: {"model": ErrorBody}, 401: {"model": ErrorBody}, 413: {"model": ErrorBody}},
)
async def upload_documents(
    request: Request,
    response: Response,
    user: CurrentUser,
    ingestion: IngestionDep,
    registry: RegistryDep,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> UploadResponse:
    """Always 200 with one result per file; only request-level faults are 4xx."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        raise ApiError(413, "too_large", "Request is too large")
    if not files:
        raise ApiError(400, "no_files", "At least one file is required in field 'files'")
    if len(files) > MAX_FILES:
        raise ApiError(400, "too_many_files", f"At most {MAX_FILES} files per request")
    results = []
    total = 0
    for f in files:
        try:
            res, n = await process_upload(
                f, user, ingestion, registry, MAX_REQUEST_BYTES - total
            )
        except RequestTooLarge:
            raise ApiError(413, "too_large", "Request is too large") from None
        total += n
        results.append(res)
    # When every file failed upload validation, surface it in the status line too
    # (413 oversize, else 415); the body keeps the per-file shape. Mixed batches stay 200.
    codes = [r.error.code if r.error else None for r in results]
    if codes and all(c in _VALIDATION_CODES for c in codes):
        response.status_code = 413 if "too_large" in codes else 415
    return UploadResponse(results=results)


@router.get("", response_model=DocumentList, responses={401: {"model": ErrorBody}})
async def list_documents(
    user: CurrentUser,
    registry: RegistryDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentList:
    docs = await run_blocking(registry.list_by_owner, user)
    return DocumentList(items=[to_out(d) for d in docs[offset : offset + limit]], total=len(docs))


@router.get("/{doc_id}", response_model=DocumentOut, responses=_ERRORS)
async def get_document(doc_id: str, user: CurrentUser, registry: RegistryDep) -> DocumentOut:
    doc = await run_blocking(registry.get, user, doc_id)
    if doc is None:
        raise ApiError(404, "not_found", "Document not found")
    return to_out(doc)


@router.delete(
    "/{doc_id}",
    status_code=204,
    responses={**_ERRORS, 409: {"model": ErrorBody}},
)
async def delete_document(doc_id: str, user: CurrentUser, ingestion: IngestionDep) -> Response:
    """404 for unknown or foreign ids (never 403); DocumentBusyError maps to 409."""
    if not await ingestion.delete(user, doc_id):
        raise ApiError(404, "not_found", "Document not found")
    return Response(status_code=204)

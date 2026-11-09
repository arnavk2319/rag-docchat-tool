"""Document endpoints: upload (multi-file), list, get, delete."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile

from ..core.concurrency import run_blocking
from ..store.registry import Document
from .deps import (
    ApiError,
    CurrentTenant,
    CurrentUser,
    IngestionDep,
    ParentsDep,
    RegistryDep,
    StoreDep,
    get_ingest_gate,
    upload_rate_limit,
)
from .schemas import (
    ChunkPassage,
    DocumentList,
    DocumentOut,
    ErrorBody,
    Highlight,
    UploadResponse,
)
from .uploads import RequestTooLarge, process_upload, safe_reason

router = APIRouter(prefix="/v1/documents", tags=["documents"])

MAX_FILES = 10
MAX_REQUEST_BYTES = 50 * 1024 * 1024
_VALIDATION_CODES = {"too_large", "bad_extension", "content_mismatch", "bad_content_type"}
_ERRORS = {401: {"model": ErrorBody}, 404: {"model": ErrorBody}}


def to_out(d: Document) -> DocumentOut:
    out = DocumentOut(**d.model_dump(exclude={"owner_id"}))
    out.failure_reason = safe_reason(out.failure_reason)  # legacy rows may hold a class name
    return out


@router.post(
    "",
    response_model=UploadResponse,
    dependencies=[Depends(upload_rate_limit)],
    responses={
        400: {"model": ErrorBody}, 401: {"model": ErrorBody}, 413: {"model": ErrorBody},
        429: {"model": ErrorBody},
    },
)
async def upload_documents(
    request: Request,
    response: Response,
    user: CurrentUser,
    ingestion: IngestionDep,
    registry: RegistryDep,
    tenant: CurrentTenant,
    gate: Annotated[asyncio.Semaphore, Depends(get_ingest_gate)],
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
                f, user, ingestion, registry, MAX_REQUEST_BYTES - total,
                tenant=tenant, gate=gate,
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


PASSAGE_CAP = 4000


@router.get("/{doc_id}/chunks/{chunk_id}", response_model=ChunkPassage, responses=_ERRORS)
async def get_chunk_passage(
    doc_id: str, chunk_id: str, user: CurrentUser, registry: RegistryDep,
    store: StoreDep, parents: ParentsDep,
) -> ChunkPassage:
    """Parent passage containing a cited chunk. Every miss is 404 (never 403)."""
    doc = await run_blocking(registry.get, user, doc_id)
    if doc is None:
        raise ApiError(404, "not_found", "Chunk not found")
    hits = await run_blocking(store.get, user, [chunk_id])
    hit = next((h for h in hits if h.chunk_id == chunk_id), None)
    if hit is None or hit.metadata.get("doc_id") != doc_id:
        raise ApiError(404, "not_found", "Chunk not found")
    pid = hit.metadata.get("parent_id")
    found = await run_blocking(parents.get_parents, user, [str(pid)]) if pid else []
    if not found or found[0].doc_id != doc_id:
        raise ApiError(404, "not_found", "Chunk not found")
    parent = found[0]
    full = parent.text
    try:
        rs = int(hit.metadata["char_start"]) - parent.char_start
        re_ = int(hit.metadata["char_end"]) - parent.char_start
    except (KeyError, TypeError, ValueError):
        rs = re_ = -1
    if not (0 <= rs < re_ <= len(full)):  # offsets unusable: locate the chunk text instead
        i = full.find(hit.text) if hit.text else -1
        rs, re_ = (i, i + len(hit.text)) if i >= 0 else (0, len(full))
    win = min(rs, max(0, len(full) - PASSAGE_CAP)) if re_ > PASSAGE_CAP else 0
    text = full[win : win + PASSAGE_CAP]
    start = rs - win
    end = min(re_ - win, len(text))
    if not 0 <= start < end:  # empty passage or degenerate range
        raise ApiError(404, "not_found", "Chunk not found")
    return ChunkPassage(
        doc_id=doc_id, chunk_id=chunk_id, filename=doc.filename, page=parent.page,
        sheet=parent.sheet, text=text, highlight=Highlight(start=start, end=end),
    )


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

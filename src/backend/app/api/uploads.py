"""Per-file upload pipeline: validate -> temp file -> ingest -> UploadResult.

Behaviour moved from the legacy router: allowlisted extension, streamed 10 MB cap, content
sniffing with zip limits, extension/content agreement, basename-only filename, temp file
named independently of the client and removed in `finally`.
"""

import hashlib
import logging
import os
import tempfile

from fastapi import UploadFile

from ..core.concurrency import run_blocking
from ..core.security import (
    UploadRejected,
    extension_of,
    read_limited,
    safe_filename,
    validate_content,
)
from ..services.ingestion import IngestionService
from ..store.registry import Registry
from .schemas import UploadError, UploadResult

log = logging.getLogger(__name__)

class RequestTooLarge(UploadRejected):
    """Running request total exceeded the per-request cap (HTTP 413, not a per-file result)."""


_FAIL_DETAIL = {
    "parse_error": "File could not be parsed",
    "empty_document": "Document contains no text",
}


def _reject(filename: str, e: UploadRejected) -> UploadResult:
    return UploadResult(
        filename=filename, ok=False, error=UploadError(code=e.code, detail=e.detail)
    )


def _write_temp(contents: bytes, ext: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(contents)
        return tmp.name


async def process_upload(
    file: UploadFile,
    owner_id: str,
    ingestion: IngestionService,
    registry: Registry,
    budget_left: int | None = None,
) -> tuple[UploadResult, int]:
    """Validate and ingest one file. Returns (result, bytes_read); never raises for bad input.

    `budget_left` caps this file's read (request-level 50 MB total); exceeding it raises
    UploadRejected('request_too_large') which the caller turns into HTTP 413.
    """
    name = safe_filename(file.filename or "")
    tmp_path: str | None = None
    size = 0
    try:
        if not name:
            raise UploadRejected("bad_extension", "Filename is required")
        ext = extension_of(name)
        contents = await read_limited(file)
        size = len(contents)
        if budget_left is not None and size > budget_left:
            raise RequestTooLarge("request_too_large", "Request is too large")
        mime = validate_content(contents, ext)
        sha = hashlib.sha256(contents).hexdigest()
        existing = await run_blocking(registry.get_by_sha256, owner_id, sha)
        tmp_path = await run_blocking(_write_temp, contents, ext)
        doc = await ingestion.ingest(owner_id, tmp_path, name, mime, size)
        dedup = existing is not None and existing.status == "ready" and doc.status == "ready"
        if doc.status == "failed":
            reason = doc.failure_reason or "ingest_failed"
            code = reason if reason in _FAIL_DETAIL else "ingest_failed"
            return (
                UploadResult(
                    filename=name,
                    ok=False,
                    doc_id=doc.doc_id,
                    status="failed",
                    failure_reason=doc.failure_reason,
                    error=UploadError(code=code, detail=_FAIL_DETAIL.get(code, "Ingestion failed")),
                ),
                size,
            )
        return (
            UploadResult(
                filename=name,
                ok=doc.status == "ready",
                doc_id=doc.doc_id,
                status=doc.status,
                deduplicated=dedup,
            ),
            size,
        )
    except RequestTooLarge:
        raise
    except UploadRejected as e:
        return _reject(name or (file.filename or ""), e), size
    except Exception as e:  # noqa: BLE001
        log.error("upload ingest failed err=%s", type(e).__name__)
        return (
            _reject(name, UploadRejected("ingest_failed", "Ingestion failed")),
            size,
        )
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass

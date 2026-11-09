"""Per-file upload pipeline: validate -> temp file -> ingest -> UploadResult.

Behaviour moved from the legacy router: allowlisted extension, streamed 10 MB cap, content
sniffing with zip limits, extension/content agreement, basename-only filename, temp file
named independently of the client and removed in `finally`.
"""

import asyncio
import hashlib
import logging
import os
import tempfile
from datetime import UTC, datetime

from fastapi import UploadFile

from ..core import security
from ..core.concurrency import run_blocking
from ..core.config import get_settings
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


SAFE_REASONS = frozenset({"parse_error", "empty_document", "cancelled", "ingest_failed"})


def safe_reason(reason: str | None) -> str | None:
    """Only fixed codes reach clients; legacy/unknown values (e.g. a class name) become ingest_failed."""
    return None if reason is None else (reason if reason in SAFE_REASONS else "ingest_failed")


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


_FAR_FUTURE = datetime(9999, 1, 1, tzinfo=UTC)


async def _check_quota(registry: Registry, owner_id: str, tenant: str | None, size: int) -> None:
    """Per-owner document cap (anon- owners: ANON_MAX_DOCS, others OWNER_MAX_DOCS, 0 = off) and,
    in the demo tenant, a total stored-bytes cap. Re-uploads of existing bytes are exempt (caller)."""
    s = get_settings()
    cap = s.anon_max_docs if owner_id.startswith("anon-") else s.owner_max_docs
    if cap and len(await run_blocking(registry.list_by_owner, owner_id)) >= cap:
        raise UploadRejected("doc_limit_reached", "Document limit reached; delete a document first")
    if tenant == s.demo_tenant_id:
        docs = await run_blocking(registry.list_older_than, "", _FAR_FUTURE, 100_000)
        if sum(d.size for d in docs) + size > s.demo_tenant_max_bytes:
            raise UploadRejected("storage_limit_reached", "Storage limit reached; try again later")


async def process_upload(
    file: UploadFile,
    owner_id: str,
    ingestion: IngestionService,
    registry: Registry,
    budget_left: int | None = None,
    *,
    tenant: str | None = None,
    gate: asyncio.Semaphore | None = None,
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
        contents = await read_limited(file, min(get_settings().max_upload_mb * 1024 * 1024, security.MAX_FILE_SIZE))
        size = len(contents)
        if budget_left is not None and size > budget_left:
            raise RequestTooLarge("request_too_large", "Request is too large")
        mime = validate_content(contents, ext)
        sha = hashlib.sha256(contents).hexdigest()
        existing = await run_blocking(registry.get_by_sha256, owner_id, sha)
        if existing is None:
            await _check_quota(registry, owner_id, tenant, size)
        tmp_path = await run_blocking(_write_temp, contents, ext)
        if gate is None:
            doc = await ingestion.ingest(owner_id, tmp_path, name, mime, size)
        else:
            async with gate:  # at most INGEST_MAX_CONCURRENT ingests; others wait
                doc = await ingestion.ingest(owner_id, tmp_path, name, mime, size)
        dedup = existing is not None and existing.status == "ready" and doc.status == "ready"
        if doc.status == "failed":
            reason = safe_reason(doc.failure_reason) or "ingest_failed"
            code = reason if reason in _FAIL_DETAIL else "ingest_failed"
            return (
                UploadResult(
                    filename=name,
                    ok=False,
                    doc_id=doc.doc_id,
                    status="failed",
                    failure_reason=reason,
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

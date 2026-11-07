"""Ingestion: parse -> chunk -> embed -> store, with registry status transitions.

Retry policy (dedupe hit on (owner_id, sha256)):
- ready: return the existing doc; no parsing, no embedding.
- failed: purge any partial vectors, re-ingest (status goes failed -> processing).
- pending/processing not in flight in this process: treated as stuck (single-process app,
  ADR-0001), purged and re-ingested. If in flight in this process, return it unchanged.
Empty/whitespace-only documents end failed (reason empty_document), never ready.
Cancelled ingests are marked failed (reason cancelled) and purged, then CancelledError re-raised.
Failure reasons stored/logged are short codes, never document text.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
from typing import Any

from app.core.concurrency import run_blocking
from app.store.chroma import VectorStore
from app.store.registry import Document, Registry
from app.utilities.file_handler import ParsedPage, ParseError, parse_uploaded_file

log = logging.getLogger(__name__)

CHUNK_CHARS = 1000
CHUNK_OVERLAP = 150
EMBED_BATCH = 32

_inflight: set[str] = set()
_inflight_lock = threading.Lock()


class EmptyDocumentError(Exception):
    """Parsed fine but contains no text."""


class DocumentBusyError(Exception):
    """The document is being ingested in this process; retry after it finishes."""


class Chunk:
    __slots__ = ("chunk_id", "metadata", "text")

    def __init__(self, chunk_id: str, text: str, metadata: dict[str, Any]) -> None:
        self.chunk_id, self.text, self.metadata = chunk_id, text, metadata


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def chunk_pages(
    pages: list[ParsedPage],
    doc_id: str,
    owner_id: str,
    size: int = CHUNK_CHARS,
    overlap: int = CHUNK_OVERLAP,
) -> list[Chunk]:
    """Fixed-size windows with overlap; char_start/char_end index into each page's text."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    out: list[Chunk] = []
    for pg in pages:
        text, start = pg.text, 0
        while start < len(text):
            end = min(start + size, len(text))
            piece = text[start:end]
            if piece.strip():
                meta: dict[str, Any] = {
                    "doc_id": doc_id,
                    "owner_id": owner_id,
                    "chunk_id": f"{doc_id}:{len(out)}",
                    "char_start": start,
                    "char_end": end,
                    "source": pg.source,
                }
                if pg.page is not None:
                    meta["page"] = pg.page
                if pg.sheet is not None:
                    meta["sheet"] = pg.sheet
                out.append(Chunk(meta["chunk_id"], piece, meta))
            if end == len(text):
                break
            start = end - overlap
    return out


def _encode(embedder: Any, texts: list[str]) -> list[list[float]]:
    return [[float(x) for x in v] for v in embedder.encode(texts)]


class IngestionService:
    def __init__(self, registry: Registry, store: VectorStore, embedder: Any | None = None) -> None:
        self._reg, self._store, self._embedder = registry, store, embedder

    def _emb(self) -> Any:
        if self._embedder is None:
            from app.core.models import get_models

            self._embedder = get_models().embedder
        return self._embedder

    async def ingest(
        self, owner_id: str, path: str, filename: str, mime: str, size: int | None = None
    ) -> Document:
        sha = await run_blocking(sha256_file, path)
        if size is None:
            size = await run_blocking(os.path.getsize, path)
        doc, created = await run_blocking(
            self._reg.create_if_absent, owner_id, filename, mime, size, sha
        )
        if not created:
            if doc.status == "ready":
                return doc
            with _inflight_lock:
                if doc.doc_id in _inflight:
                    return doc
            await run_blocking(self._store.delete_doc, owner_id, doc.doc_id)
        with _inflight_lock:
            if doc.doc_id in _inflight:
                return doc
            _inflight.add(doc.doc_id)
        try:
            await self._run(doc, path)
        finally:
            with _inflight_lock:
                _inflight.discard(doc.doc_id)
        return self._reg.get(owner_id, doc.doc_id) or doc

    async def _run(self, doc: Document, path: str) -> None:
        owner = doc.owner_id
        await run_blocking(self._reg.update_status, owner, doc.doc_id, "processing")
        try:
            pages = await run_blocking(parse_uploaded_file, path, doc.mime)
            await run_blocking(self._reg.update_pages_or_sheets, owner, doc.doc_id, len(pages))
            chunks = chunk_pages(pages, doc.doc_id, owner)
            if not chunks:
                raise EmptyDocumentError
            for i in range(0, len(chunks), EMBED_BATCH):
                batch = chunks[i : i + EMBED_BATCH]
                vecs = await run_blocking(_encode, self._emb(), [c.text for c in batch])
                await run_blocking(
                    self._store.add,
                    [c.chunk_id for c in batch],
                    vecs,
                    [c.text for c in batch],
                    [c.metadata for c in batch],
                )
        except BaseException as e:
            if isinstance(e, ParseError):
                reason = "parse_error"
            elif isinstance(e, EmptyDocumentError):
                reason = "empty_document"
            elif isinstance(e, asyncio.CancelledError):
                reason = "cancelled"
            else:
                reason = type(e).__name__
            self._fail(doc, reason)
            if not isinstance(e, Exception):
                raise
            return
        await run_blocking(self._reg.update_status, owner, doc.doc_id, "ready")

    def _fail(self, doc: Document, reason: str) -> None:
        """Purge partial vectors and mark failed. Synchronous so it also runs when cancelled.

        Each step is attempted independently; errors are logged by class name only.
        """
        log.warning("ingest failed doc=%s reason=%s", doc.doc_id, reason)
        for step, args in (
            (self._store.delete_doc, (doc.owner_id, doc.doc_id)),
            (self._reg.update_status, (doc.owner_id, doc.doc_id, "failed")),
        ):
            try:
                step(*args)
            except Exception as e:  # noqa: BLE001
                log.error("ingest cleanup step failed doc=%s err=%s", doc.doc_id, type(e).__name__)

    async def delete(self, owner_id: str, doc_id: str) -> bool:
        """Remove vectors then the registry row. Foreign owner: False, nothing touched.

        Raises DocumentBusyError while the doc is mid-ingest in this process."""
        if await run_blocking(self._reg.get, owner_id, doc_id) is None:
            return False
        with _inflight_lock:
            if doc_id in _inflight:
                raise DocumentBusyError(doc_id)
        await run_blocking(self._store.delete_doc, owner_id, doc_id)
        return await run_blocking(self._reg.delete, owner_id, doc_id)

"""Ingestion: parse -> parent/child chunk -> embed children -> store, with registry status transitions.

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
import copy
import hashlib
import logging
import os
import threading
from typing import Any

from app.core.concurrency import run_blocking
from app.objectstore import ObjectStore, original_key
from app.services.chunking import chunk_document
from app.store.bm25 import Bm25Store
from app.store.parents import Parent, SqliteParents
from app.store.registry import Document, Registry, SqliteRegistry
from app.utilities.file_handler import ParsedPage, ParseError, parse_uploaded_file
from app.vectorstore import DEFAULT_TENANT, ChunkRecord, VectorStore

log = logging.getLogger(__name__)

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


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def chunk_pages(pages: list[ParsedPage], doc_id: str, owner_id: str) -> tuple[list[Parent], list[Chunk]]:
    """Parent-child split (see services/chunking.py). Returns (parents, embeddable children)."""
    parents: list[Parent] = []
    children: list[Chunk] = []
    for p in chunk_document(pages, doc_id):
        parents.append(
            Parent(
                owner_id=owner_id, doc_id=doc_id, parent_id=p.parent_id, page=p.page,
                sheet=p.sheet, section=p.section, char_start=p.char_start,
                char_end=p.char_end, text=p.text,
            )
        )
        for c in p.children:
            meta: dict[str, Any] = {
                "doc_id": doc_id,
                "owner_id": owner_id,
                "chunk_id": c.chunk_id,
                "parent_id": c.parent_id,
                "char_start": c.char_start,
                "char_end": c.char_end,
                "source": p.source,
                "section": c.section,
            }
            if p.page is not None:
                meta["page"] = p.page
            if p.sheet is not None:
                meta["sheet"] = p.sheet
            children.append(Chunk(c.chunk_id, c.text, meta))
    return parents, children


def _encode(embedder: Any, texts: list[str]) -> list[list[float]]:
    return [[float(x) for x in v] for v in embedder.encode(texts)]


class IngestionService:
    def __init__(
        self,
        registry: Registry,
        store: VectorStore,
        embedder: Any | None = None,
        parents: SqliteParents | None = None,
        bm25: Bm25Store | None = None,
        tenant_id: str = DEFAULT_TENANT,
        objects: ObjectStore | None = None,
    ) -> None:
        self._objects = objects
        self._reg, self._store, self._embedder = registry, store, embedder
        self._tenant = tenant_id
        self._bm25 = bm25
        if parents is None:
            # Parents share the registry's SQLite file (same DB_PATH).
            if not isinstance(registry, SqliteRegistry):
                raise TypeError("pass an explicit parents store for non-SQLite registries")
            parents = SqliteParents(registry.db_path)
        self._parents = parents

    def for_tenant(self, tenant_id: str) -> IngestionService:
        """Copy bound to the authenticated tenant (registry, vectors, parents, BM25 all scoped)."""
        v = copy.copy(self)
        v._reg, v._tenant = self._reg.for_tenant(tenant_id), tenant_id
        v._parents = self._parents.for_tenant(tenant_id)
        v._bm25 = self._bm25.for_tenant(tenant_id) if self._bm25 is not None else None
        return v

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
            await self._purge(owner_id, doc.doc_id)
        with _inflight_lock:
            if doc.doc_id in _inflight:
                return doc
            _inflight.add(doc.doc_id)
        try:
            await self._run(doc, path)
        finally:
            with _inflight_lock:
                _inflight.discard(doc.doc_id)
        final = self._reg.get(owner_id, doc.doc_id) or doc
        if final.status == "ready":
            await self._persist_original(final, path)
        return final

    def original_key(self, doc: Document) -> str:
        return original_key(self._tenant, doc.doc_id, doc.filename)

    async def _persist_original(self, doc: Document, path: str) -> None:
        """Store the uploaded original (idempotent). Best effort: a store fault is logged only."""
        if self._objects is None:
            return
        key = self.original_key(doc)
        try:
            if await self._objects.exists(self._tenant, key):
                return
            data = await run_blocking(_read_bytes, path)
            await self._objects.put(self._tenant, key, data)
        except Exception as e:  # noqa: BLE001
            log.error("original persist failed doc=%s err=%s", doc.doc_id, type(e).__name__)

    async def _run(self, doc: Document, path: str) -> None:
        owner = doc.owner_id
        await run_blocking(self._reg.update_status, owner, doc.doc_id, "processing")
        try:
            pages = await run_blocking(parse_uploaded_file, path, doc.mime)
            await run_blocking(self._reg.update_pages_or_sheets, owner, doc.doc_id, len(pages))
            parents, chunks = chunk_pages(pages, doc.doc_id, owner)
            if not chunks:
                raise EmptyDocumentError
            await run_blocking(self._parents.replace_doc, owner, doc.doc_id, parents)
            for i in range(0, len(chunks), EMBED_BATCH):
                batch = chunks[i : i + EMBED_BATCH]
                vecs = await run_blocking(_encode, self._emb(), [c.text for c in batch])
                await self._store.upsert_chunks(
                    self._tenant, owner, doc.doc_id,
                    [ChunkRecord(chunk_id=c.chunk_id, text=c.text, metadata=c.metadata) for c in batch],
                    vecs,
                )
            if self._bm25 is not None:
                await run_blocking(
                    self._bm25.add_doc, owner, doc.doc_id,
                    [c.chunk_id for c in chunks], [c.text for c in chunks],
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
            await self._fail(doc, reason)
            if not isinstance(e, Exception):
                raise
            return
        await run_blocking(self._reg.update_status, owner, doc.doc_id, "ready")

    async def _purge(self, owner_id: str, doc_id: str) -> None:
        await self._store.delete_doc(self._tenant, owner_id, doc_id)
        await run_blocking(self._parents.delete_doc, owner_id, doc_id)
        if self._bm25 is not None:
            await run_blocking(self._bm25.delete_doc, owner_id, doc_id)

    async def _fail(self, doc: Document, reason: str) -> None:
        """Purge partial vectors and mark failed; also runs after a cancellation.

        Each step is attempted independently; errors are logged by class name only.
        """
        log.warning("ingest failed doc=%s reason=%s", doc.doc_id, reason)
        try:
            await self._store.delete_doc(self._tenant, doc.owner_id, doc.doc_id)
        except Exception as e:  # noqa: BLE001
            log.error("ingest cleanup step failed doc=%s err=%s", doc.doc_id, type(e).__name__)
        for step, args in (
            (self._parents.delete_doc, (doc.owner_id, doc.doc_id)),
            *(((self._bm25.delete_doc, (doc.owner_id, doc.doc_id)),) if self._bm25 else ()),
            (self._reg.update_status, (doc.owner_id, doc.doc_id, "failed")),
            (self._reg.set_failure_reason, (doc.owner_id, doc.doc_id, reason)),
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
        doc = await run_blocking(self._reg.get, owner_id, doc_id)
        await self._purge(owner_id, doc_id)
        if self._objects is not None and doc is not None:
            await self._objects.delete(self._tenant, self.original_key(doc))
        return await run_blocking(self._reg.delete, owner_id, doc_id)

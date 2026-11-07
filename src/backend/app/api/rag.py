"""Shared helpers for summarize/query: doc resolution, embedding, prompt context building."""

import re
from collections.abc import Sequence
from typing import Any

from ..core.concurrency import run_blocking
from ..core.models import get_models
from ..store.chroma import Hit
from ..store.parents import SqliteParents
from ..store.registry import Document, Registry
from .deps import ApiError
from .schemas import Citation, Span

_TAG = re.compile(r"[<>]")


def escape(text: str) -> str:
    """Neutralise angle brackets so retrieved text cannot close or forge prompt tags."""
    return _TAG.sub(lambda m: "&lt;" if m.group() == "<" else "&gt;", text)


async def resolve_ready_docs(registry: Registry, owner: str, doc_ids: Sequence[str]) -> list[Document]:
    """Owner-scoped lookup: unknown/foreign -> 404, not ready -> 409."""
    docs = []
    for did in doc_ids:
        d = await run_blocking(registry.get, owner, did)
        if d is None:
            raise ApiError(404, "not_found", "Document not found")
        if d.status != "ready":
            raise ApiError(409, "not_ready", "Document is not ready")
        docs.append(d)
    return docs


async def embed_one(text: str) -> list[float]:
    embedder = get_models().embedder
    vecs = await run_blocking(lambda: embedder.encode([text]))
    return [float(x) for x in vecs[0]]


async def doc_text(parents: SqliteParents, owner: str, doc_id: str, max_chars: int) -> str:
    """The document's parent texts in document order, joined and cut at max_chars."""
    plist = await run_blocking(parents.list_doc_parents, owner, doc_id)
    parts: list[str] = []
    used = 0
    for p in plist:
        if used >= max_chars:
            break
        parts.append(p.text[: max_chars - used])
        used += len(parts[-1])
    return "\n".join(parts)


def citation_for(hit: Hit, filename: str) -> Citation:
    m: dict[str, Any] = hit.metadata
    page = m.get("page")
    sheet = m.get("sheet")
    return Citation(
        doc_id=str(m["doc_id"]),
        filename=filename,
        chunk_id=hit.chunk_id,
        page=int(page) if page is not None else None,
        sheet=str(sheet) if sheet is not None else None,
        span=Span(char_start=int(m.get("char_start", 0)), char_end=int(m.get("char_end", 0))),
    )

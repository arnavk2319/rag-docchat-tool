"""VectorStore interface. Tenant and owner scoping are mandatory parameters (ADR-0003 s1)."""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

EMBEDDING_DIM = 384
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Chunk ids are "<doc>:<parent>:<n>" style (app/services/chunking.py); only ever bound as parameters.
_CHUNK_ID_RE = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")


class ChunkRecord(BaseModel):
    chunk_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoredChunk(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float  # cosine similarity, higher is better


def check_id(name: str, value: str) -> str:
    rx = _CHUNK_ID_RE if name == "chunk_id" else _ID_RE
    if not isinstance(value, str) or not rx.fullmatch(value):
        raise ValueError(f"invalid {name}")
    return value


@runtime_checkable
class VectorStore(Protocol):
    async def upsert_chunks(
        self,
        tenant_id: str,
        owner_id: str,
        doc_id: str,
        chunks: list[ChunkRecord],
        embeddings: list[list[float]],
    ) -> None: ...

    async def query(
        self,
        embedding: list[float],
        k: int,
        tenant_id: str,
        owner_id: str,
        doc_ids: list[str] | None = None,
    ) -> list[ScoredChunk]: ...

    async def delete_doc(self, tenant_id: str, owner_id: str, doc_id: str) -> int: ...

    async def get_chunks(
        self, tenant_id: str, owner_id: str, chunk_ids: list[str]
    ) -> list[ScoredChunk]: ...

    async def list_chunks(
        self, tenant_id: str, owner_id: str, doc_ids: list[str] | None = None
    ) -> list[ScoredChunk]: ...

    async def count(
        self, tenant_id: str, owner_id: str | None = None, doc_id: str | None = None
    ) -> int: ...


def validate_upsert(
    tenant_id: str,
    owner_id: str,
    doc_id: str,
    chunks: list[ChunkRecord],
    embeddings: list[list[float]],
) -> None:
    check_id("tenant_id", tenant_id)
    check_id("owner_id", owner_id)
    check_id("doc_id", doc_id)
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings length mismatch")
    for e in embeddings:
        if len(e) != EMBEDDING_DIM:
            raise ValueError(f"embedding must have {EMBEDDING_DIM} dims")
    if len({c.chunk_id for c in chunks}) != len(chunks):
        raise ValueError("duplicate chunk_id")
    for c in chunks:
        check_id("chunk_id", c.chunk_id)


def validate_query(
    embedding: list[float],
    k: int,
    tenant_id: str,
    owner_id: str,
    doc_ids: list[str] | None,
) -> None:
    check_id("tenant_id", tenant_id)
    check_id("owner_id", owner_id)
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(f"embedding must have {EMBEDDING_DIM} dims")
    if not 1 <= k <= 200:
        raise ValueError("k must be in 1..200")
    for d in doc_ids or []:
        check_id("doc_id", d)

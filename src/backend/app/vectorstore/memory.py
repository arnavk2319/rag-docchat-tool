"""In-memory VectorStore (numpy cosine). Used by unit tests; no server needed."""

from __future__ import annotations

import numpy as np

from .base import (
    ChunkRecord,
    ScoredChunk,
    check_id,
    validate_query,
    validate_upsert,
)


class InMemoryVectorStore:
    def __init__(self) -> None:
        # key: (tenant, owner, doc, chunk_id) -> (record, vector)
        self._rows: dict[tuple[str, str, str, str], tuple[ChunkRecord, np.ndarray]] = {}

    async def upsert_chunks(
        self, tenant_id, owner_id, doc_id, chunks, embeddings
    ) -> None:
        validate_upsert(tenant_id, owner_id, doc_id, chunks, embeddings)
        for c, e in zip(chunks, embeddings, strict=True):
            self._rows[(tenant_id, owner_id, doc_id, c.chunk_id)] = (
                c.model_copy(deep=True),
                np.asarray(e, dtype=np.float32),
            )

    async def query(
        self, embedding, k, tenant_id, owner_id, doc_ids=None
    ) -> list[ScoredChunk]:
        validate_query(embedding, k, tenant_id, owner_id, doc_ids)
        q = np.asarray(embedding, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        scored: list[ScoredChunk] = []
        for (t, o, d, _), (rec, vec) in self._rows.items():
            if t != tenant_id or o != owner_id:
                continue
            if doc_ids is not None and d not in doc_ids:
                continue
            denom = qn * float(np.linalg.norm(vec))
            sim = float(q @ vec) / denom if denom else 0.0
            scored.append(
                ScoredChunk(
                    chunk_id=rec.chunk_id,
                    tenant_id=tenant_id,
                    doc_id=d,
                    text=rec.text,
                    metadata=dict(rec.metadata),
                    score=sim,
                )
            )
        scored.sort(key=lambda s: (-s.score, s.chunk_id))
        return scored[:k]

    async def delete_doc(self, tenant_id, owner_id, doc_id) -> int:
        check_id("tenant_id", tenant_id)
        check_id("owner_id", owner_id)
        check_id("doc_id", doc_id)
        keys = [k for k in self._rows if k[:3] == (tenant_id, owner_id, doc_id)]
        for k in keys:
            del self._rows[k]
        return len(keys)

    async def get_chunks(self, tenant_id, owner_id, chunk_ids) -> list[ScoredChunk]:
        check_id("tenant_id", tenant_id)
        check_id("owner_id", owner_id)
        want = set(chunk_ids)
        return [
            c for c in await self.list_chunks(tenant_id, owner_id) if c.chunk_id in want
        ]

    async def list_chunks(self, tenant_id, owner_id, doc_ids=None) -> list[ScoredChunk]:
        """All stored chunks (score 0.0) for the owner, ordered by (doc_id, chunk_id)."""
        check_id("tenant_id", tenant_id)
        check_id("owner_id", owner_id)
        out = [
            ScoredChunk(
                chunk_id=rec.chunk_id, doc_id=d, text=rec.text, tenant_id=tenant_id,
                metadata=dict(rec.metadata), score=0.0,
            )
            for (t, o, d, _), (rec, _v) in self._rows.items()
            if t == tenant_id and o == owner_id and (doc_ids is None or d in doc_ids)
        ]
        out.sort(key=lambda c: (c.doc_id, c.chunk_id))
        return out

    async def count(self, tenant_id, owner_id=None, doc_id=None) -> int:
        check_id("tenant_id", tenant_id)
        if owner_id is not None:
            check_id("owner_id", owner_id)
        if doc_id is not None:
            check_id("doc_id", doc_id)
        return sum(
            1
            for (t, o, d, _) in self._rows
            if t == tenant_id
            and (owner_id is None or o == owner_id)
            and (doc_id is None or d == doc_id)
        )

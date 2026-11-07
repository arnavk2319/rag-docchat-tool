"""Embedded Chroma vector store (PersistentClient, cosine). Synchronous; wrap in run_blocking.

Vectors are always passed explicitly (embedding_function=None) so Chroma never downloads
a default model. The client is shared across run_blocking worker threads; single-process
is the supported mode. Every query/delete/count is owner-filtered.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

COLLECTION = "chunks"


class Hit(BaseModel):
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    distance: float


class VectorStore(Protocol):
    def add(
        self,
        ids: Sequence[str],
        embeddings: Sequence[Sequence[float]],
        texts: Sequence[str],
        metadatas: Sequence[dict[str, Any]],
    ) -> None: ...
    def query(
        self,
        owner_id: str,
        embedding: Sequence[float],
        k: int = 5,
        doc_id: str | None = None,
    ) -> list[Hit]: ...
    def delete_doc(self, owner_id: str, doc_id: str) -> None: ...
    def count(self, owner_id: str, doc_id: str | None = None) -> int: ...


def _where(owner_id: str, doc_id: str | None) -> dict[str, Any]:
    if not owner_id:
        raise ValueError("owner_id is required")
    if doc_id is None:
        return {"owner_id": owner_id}
    return {"$and": [{"owner_id": owner_id}, {"doc_id": doc_id}]}


class ChromaStore:
    def __init__(self, path: str | Path | None = None) -> None:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        if path is None:
            from app.core.config import get_settings

            path = get_settings().chroma_dir
        Path(path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._col = self._client.get_or_create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )

    def add(self, ids, embeddings, texts, metadatas) -> None:
        if not ids:
            return
        for m in metadatas:
            if not m.get("owner_id") or not m.get("doc_id"):
                raise ValueError("every chunk needs owner_id and doc_id metadata")
        self._col.upsert(
            ids=list(ids),
            embeddings=[list(map(float, e)) for e in embeddings],
            documents=list(texts),
            metadatas=list(metadatas),
        )

    def query(self, owner_id, embedding, k=5, doc_id=None) -> list[Hit]:
        where = _where(owner_id, doc_id)
        n = self.count(owner_id, doc_id)
        if n == 0 or k <= 0:
            return []
        res = self._col.query(
            query_embeddings=[list(map(float, embedding))],
            n_results=min(k, n),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        return [
            Hit(chunk_id=i, text=t, metadata=dict(m), distance=d)
            for i, t, m, d in zip(
                res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]
            )
        ]

    def delete_doc(self, owner_id, doc_id) -> None:
        self._col.delete(where=_where(owner_id, doc_id))

    def count(self, owner_id, doc_id=None) -> int:
        return len(self._col.get(where=_where(owner_id, doc_id), include=[])["ids"])

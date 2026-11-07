"""Hybrid retrieval: top-N BM25 + top-N dense fused with Reciprocal Rank Fusion (k=60).

Scoped to the caller's owner_id and an optional doc_id list (an empty list matches nothing).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.store.bm25 import Bm25Store
from app.vectorstore.sync import Hit, SyncVectors

RRF_K = 60
TOP_N = 20


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Fuse ranked id lists: score = sum 1/(k + rank), rank from 1. Ties break by id."""
    score: dict[str, float] = {}
    for r in rankings:
        for rank, cid in enumerate(dict.fromkeys(r), 1):
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(score.items(), key=lambda t: (-t[1], t[0]))


def rebuild_bm25(owner_id: str, store: SyncVectors, bm25: Bm25Store) -> None:
    """Rebuild the owner's BM25 index from the chunk text persisted in the vector store."""
    docs: dict[str, tuple[list[str], list[str]]] = {}
    for h in store.list_chunks(owner_id):
        ids, texts = docs.setdefault(h.metadata["doc_id"], ([], []))
        ids.append(h.chunk_id)
        texts.append(h.text)
    bm25.load_owner(owner_id, docs)


def hybrid_search(
    owner_id: str,
    question: str,
    embedding: Sequence[float],
    store: SyncVectors,
    bm25: Bm25Store | None,
    doc_ids: Sequence[str] | None = None,
    top_k: int = 5,
    top_n: int = TOP_N,
) -> list[Hit]:
    if bm25 is not None and not bm25.has_owner(owner_id):
        rebuild_bm25(owner_id, store, bm25)
    dense = store.query(owner_id, embedding, k=top_n, doc_ids=doc_ids)
    sparse = bm25.query(owner_id, question, k=top_n, doc_ids=doc_ids) if bm25 else []
    by_id = {h.chunk_id: h for h in dense}
    fused = rrf([[h.chunk_id for h in dense], [c for c, _ in sparse]])[:top_k]
    missing = [c for c, _ in fused if c not in by_id]
    if missing:
        for h in store.get(owner_id, missing):
            by_id[h.chunk_id] = h
    allowed = None if doc_ids is None else set(doc_ids)
    out = []
    for c, _ in fused:
        h = by_id.get(c)
        if h is None or (allowed is not None and h.metadata.get("doc_id") not in allowed):
            continue
        out.append(h)
    return out

"""Owner-wide in-memory BM25Okapi index (shared IDF); no files on disk.

The corpus lives in the VectorStore (chunk text per doc). The index is rebuilt lazily from it:
`has_owner` is False after a restart, and the first retrieval calls `load_owner` with the stored
chunks. add_doc/delete_doc keep this process current; a second replica refreshes on its own restart
(see docs/backlog.md). Every query/delete is owner-scoped. Synchronous; use run_blocking.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence

from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class _DocIndex:
    __slots__ = ("bm25", "chunk_ids", "tokens")

    def __init__(self, chunk_ids: list[str], tokens: list[list[str]]) -> None:
        self.chunk_ids, self.tokens = chunk_ids, tokens
        self.bm25 = BM25Okapi(tokens) if tokens else None


class Bm25Store:
    def __init__(self) -> None:
        self._idx: dict[tuple[str, str], _DocIndex] = {}
        self._loaded: set[str] = set()
        # owner -> (chunk_ids, doc_of_chunk, tokens, BM25Okapi) over ALL the owner's docs, so IDF is
        # shared and scores are comparable across documents (per-doc IDF starved small docs).
        self._merged: dict[str, tuple[list[str], list[str], list[list[str]], BM25Okapi]] = {}
        self._lock = threading.Lock()

    def has_owner(self, owner_id: str) -> bool:
        return owner_id in self._loaded

    def load_owner(self, owner_id: str, docs: dict[str, tuple[list[str], list[str]]]) -> None:
        """Rebuild the owner's index from stored chunks: {doc_id: (chunk_ids, texts)}."""
        if not owner_id:
            raise ValueError("owner_id is required")
        built = {
            (owner_id, d): _DocIndex(list(ids), [tokenize(t) for t in texts])
            for d, (ids, texts) in docs.items()
        }
        with self._lock:
            for k in [k for k in self._idx if k[0] == owner_id]:
                del self._idx[k]
            self._idx.update(built)
            self._loaded.add(owner_id)
            self._merged.pop(owner_id, None)

    def add_doc(
        self, owner_id: str, doc_id: str, chunk_ids: Sequence[str], texts: Sequence[str]
    ) -> None:
        if not owner_id or not doc_id:
            raise ValueError("owner_id and doc_id are required")
        if len(chunk_ids) != len(texts):
            raise ValueError("chunk_ids/texts length mismatch")
        idx = _DocIndex(list(chunk_ids), [tokenize(t) for t in texts])
        with self._lock:
            self._idx[(owner_id, doc_id)] = idx
            self._merged.pop(owner_id, None)

    def delete_doc(self, owner_id: str, doc_id: str) -> None:
        if not owner_id:
            raise ValueError("owner_id is required")
        with self._lock:
            self._idx.pop((owner_id, doc_id), None)
            self._merged.pop(owner_id, None)

    def has_doc(self, owner_id: str, doc_id: str) -> bool:
        return (owner_id, doc_id) in self._idx

    def query(
        self, owner_id: str, text: str, k: int = 20, doc_ids: Sequence[str] | None = None
    ) -> list[tuple[str, float]]:
        """Top-k (chunk_id, score) over the owner's docs (optionally limited to doc_ids).

        Only chunks sharing at least one query token are returned. Ties break by chunk_id."""
        if not owner_id:
            raise ValueError("owner_id is required")
        q = tokenize(text)
        if not q or k <= 0:
            return []
        with self._lock:
            m = self._merged.get(owner_id)
            if m is None:
                ids: list[str] = []
                owners: list[str] = []
                toks: list[list[str]] = []
                for (o, d), ix in sorted(self._idx.items()):
                    if o == owner_id and ix.bm25 is not None:
                        ids += ix.chunk_ids
                        owners += [d] * len(ix.chunk_ids)
                        toks += ix.tokens
                if not ids:
                    return []
                m = (ids, owners, toks, BM25Okapi(toks))
                self._merged[owner_id] = m
        ids, owners, toks, bm = m
        allowed = None if doc_ids is None else set(doc_ids)
        qset = set(q)
        scores = bm.get_scores(q)
        scored = [
            (cid, float(sc)) for cid, d, t, sc in zip(ids, owners, toks, scores, strict=True)
            if (allowed is None or d in allowed) and qset.intersection(t)
        ]
        scored.sort(key=lambda t: (-t[1], t[0]))
        return scored[:k]

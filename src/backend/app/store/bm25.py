"""Per-document BM25Okapi indexes persisted as JSON token lists (no pickle).

One file per (owner, doc): {owner_id, doc_id, chunk_ids, tokens}. Files are written
atomically (tmp + replace). The in-memory BM25Okapi objects are rebuilt from the JSON on
construction ("startup"). Every query/delete is owner-scoped. Synchronous; use run_blocking.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from collections.abc import Sequence
from pathlib import Path

from rank_bm25 import BM25Okapi

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:24]


class _DocIndex:
    __slots__ = ("bm25", "chunk_ids", "tokens")

    def __init__(self, chunk_ids: list[str], tokens: list[list[str]]) -> None:
        self.chunk_ids, self.tokens = chunk_ids, tokens
        self.bm25 = BM25Okapi(tokens) if tokens else None


class Bm25Store:
    def __init__(self, path: str | Path) -> None:
        self._dir = Path(path)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._idx: dict[tuple[str, str], _DocIndex] = {}
        self._lock = threading.Lock()
        self.load_all()

    def _file(self, owner_id: str, doc_id: str) -> Path:
        return self._dir / f"{_h(owner_id)}-{_h(doc_id)}.json"

    def load_all(self) -> int:
        """Rebuild every in-memory index from disk. Corrupt files are skipped (logged)."""
        loaded: dict[tuple[str, str], _DocIndex] = {}
        for f in self._dir.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                ids, toks = list(d["chunk_ids"]), [list(map(str, t)) for t in d["tokens"]]
                if len(ids) != len(toks):
                    raise ValueError("length mismatch")
                loaded[(str(d["owner_id"]), str(d["doc_id"]))] = _DocIndex(ids, toks)
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.warning("bm25 index skipped file=%s err=%s", f.name, type(e).__name__)
        with self._lock:
            self._idx = loaded
        return len(loaded)

    def add_doc(
        self, owner_id: str, doc_id: str, chunk_ids: Sequence[str], texts: Sequence[str]
    ) -> None:
        if not owner_id or not doc_id:
            raise ValueError("owner_id and doc_id are required")
        if len(chunk_ids) != len(texts):
            raise ValueError("chunk_ids/texts length mismatch")
        ids, toks = list(chunk_ids), [tokenize(t) for t in texts]
        path = self._file(owner_id, doc_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"owner_id": owner_id, "doc_id": doc_id, "chunk_ids": ids, "tokens": toks}),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        idx = _DocIndex(ids, toks)
        with self._lock:
            self._idx[(owner_id, doc_id)] = idx

    def delete_doc(self, owner_id: str, doc_id: str) -> None:
        if not owner_id:
            raise ValueError("owner_id is required")
        self._file(owner_id, doc_id).unlink(missing_ok=True)
        with self._lock:
            self._idx.pop((owner_id, doc_id), None)

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
            items = [
                (d, ix) for (o, d), ix in self._idx.items()
                if o == owner_id and (doc_ids is None or d in doc_ids)
            ]
        qset = set(q)
        scored: list[tuple[str, float]] = []
        for _d, ix in items:
            if ix.bm25 is None:
                continue
            scores = ix.bm25.get_scores(q)
            for cid, toks, sc in zip(ix.chunk_ids, ix.tokens, scores):
                if qset.intersection(toks):
                    scored.append((cid, float(sc)))
        scored.sort(key=lambda t: (-t[1], t[0]))
        return scored[:k]

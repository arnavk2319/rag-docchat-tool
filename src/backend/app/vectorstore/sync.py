"""Sync, tenant-bound view over an async VectorStore for worker-thread callers (retrieval, tools).

Calls hop to the app's event loop when running inside a Starlette/anyio worker thread (so a
loop-bound Postgres pool is used on its own loop); otherwise (tests, evals) they run via asyncio.run.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from collections.abc import Coroutine, Sequence
from typing import Any

from anyio import from_thread
from pydantic import BaseModel

from .base import ChunkRecord, ScoredChunk, VectorStore

MAX_K = 200  # VectorStore.query upper bound
DEFAULT_TENANT = "default"  # single-tenant until T-029 threads the verified tenant through


class Hit(BaseModel):
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    distance: float  # cosine distance = 1 - similarity


def _hit(c: ScoredChunk, *, distance: float | None = None) -> Hit:
    return Hit(
        chunk_id=c.chunk_id, text=c.text, metadata={"doc_id": c.doc_id, **c.metadata},
        distance=1.0 - c.score if distance is None else distance,
    )


def run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    async def _w() -> T:
        return await coro

    try:
        return from_thread.run(_w)
    except Exception:  # NoEventLoopError / RuntimeError when not in an anyio worker thread
        if inspect.getcoroutinestate(coro) != inspect.CORO_CREATED:
            raise  # the coroutine itself raised; do not re-run it
        # Not inside an anyio worker thread (tests, evals, scripts): own loop, in a helper thread
        # when this thread already runs one. Loop-bound resources (Postgres pools) need the worker path.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro).result()


class SyncVectors:
    def __init__(self, inner: VectorStore, tenant_id: str = DEFAULT_TENANT) -> None:
        self.inner, self.tenant_id = inner, tenant_id

    def add(
        self, ids: Sequence[str], embeddings: Sequence[Sequence[float]],
        texts: Sequence[str], metadatas: Sequence[dict[str, Any]],
    ) -> None:
        groups: dict[tuple[str, str], list[int]] = {}
        for i, m in enumerate(metadatas):
            if not m.get("owner_id") or not m.get("doc_id"):
                raise ValueError("every chunk needs owner_id and doc_id metadata")
            groups.setdefault((m["owner_id"], m["doc_id"]), []).append(i)
        for (owner, doc), idx in groups.items():
            run_sync(self.inner.upsert_chunks(
                self.tenant_id, owner, doc,
                [ChunkRecord(chunk_id=ids[i], text=texts[i], metadata=dict(metadatas[i])) for i in idx],
                [[float(x) for x in embeddings[i]] for i in idx],
            ))

    def query(
        self, owner_id: str, embedding: Sequence[float], k: int = 5,
        doc_id: str | None = None, doc_ids: Sequence[str] | None = None,
    ) -> list[Hit]:
        if doc_id is not None:
            doc_ids = [doc_id] if doc_ids is None else [d for d in doc_ids if d == doc_id]
        if k <= 0:
            return []
        k = min(k, MAX_K)
        res = run_sync(self.inner.query(
            [float(x) for x in embedding], k, self.tenant_id, owner_id,
            None if doc_ids is None else list(doc_ids),
        ))
        return [_hit(c) for c in res]

    def get(self, owner_id: str, chunk_ids: Sequence[str]) -> list[Hit]:
        ids = list(dict.fromkeys(chunk_ids))
        if not ids:
            return []
        return [_hit(c, distance=1.0) for c in run_sync(
            self.inner.get_chunks(self.tenant_id, owner_id, ids))]

    def list_chunks(self, owner_id: str, doc_ids: Sequence[str] | None = None) -> list[Hit]:
        return [_hit(c, distance=1.0) for c in run_sync(self.inner.list_chunks(
            self.tenant_id, owner_id, None if doc_ids is None else list(doc_ids)))]

    def delete_doc(self, owner_id: str, doc_id: str) -> None:
        run_sync(self.inner.delete_doc(self.tenant_id, owner_id, doc_id))

    def count(self, owner_id: str, doc_id: str | None = None) -> int:
        return run_sync(self.inner.count(self.tenant_id, owner_id, doc_id))

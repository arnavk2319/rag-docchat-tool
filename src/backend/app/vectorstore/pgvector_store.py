"""Postgres + pgvector VectorStore (psycopg3 async pool). All values are bound parameters."""

from __future__ import annotations

import json
from typing import Any

from .base import (
    ChunkRecord,
    ScoredChunk,
    check_id,
    validate_query,
    validate_upsert,
)

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS vector_chunks (
    tenant_id text NOT NULL,
    owner_id text NOT NULL,
    doc_id text NOT NULL,
    chunk_id text NOT NULL,
    text text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(384) NOT NULL,
    PRIMARY KEY (tenant_id, owner_id, doc_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS vector_chunks_embedding_hnsw
    ON vector_chunks USING hnsw (embedding vector_cosine_ops);
"""

UPSERT_SQL = (
    "INSERT INTO vector_chunks (tenant_id, owner_id, doc_id, chunk_id, text, metadata, embedding) "
    "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::vector) "
    "ON CONFLICT (tenant_id, owner_id, doc_id, chunk_id) DO UPDATE SET "
    "text = EXCLUDED.text, metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding"
)
QUERY_SQL = (
    "SELECT chunk_id, doc_id, text, metadata, 1 - (embedding <=> %s::vector) AS score "
    "FROM vector_chunks WHERE tenant_id = %s AND owner_id = %s{doc_filter} "
    "ORDER BY embedding <=> %s::vector, chunk_id LIMIT %s"
)
DELETE_SQL = (
    "DELETE FROM vector_chunks WHERE tenant_id = %s AND owner_id = %s AND doc_id = %s"
)


def vec_literal(v: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


class PgVectorStore:
    """`pool` is a psycopg_pool.AsyncConnectionPool (or any object with the same surface)."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def upsert_chunks(
        self, tenant_id, owner_id, doc_id, chunks, embeddings
    ) -> None:
        validate_upsert(tenant_id, owner_id, doc_id, chunks, embeddings)
        if not chunks:
            return
        rows = [
            (
                tenant_id,
                owner_id,
                doc_id,
                c.chunk_id,
                c.text,
                json.dumps(c.metadata),
                vec_literal(e),
            )
            for c, e in zip(chunks, embeddings, strict=True)
        ]
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.executemany(UPSERT_SQL, rows)

    async def query(
        self, embedding, k, tenant_id, owner_id, doc_ids=None
    ) -> list[ScoredChunk]:
        validate_query(embedding, k, tenant_id, owner_id, doc_ids)
        if doc_ids is not None and not doc_ids:
            return []
        lit = vec_literal(embedding)
        params: list[Any] = [lit, tenant_id, owner_id]
        doc_filter = ""
        if doc_ids is not None:
            doc_filter = " AND doc_id = ANY(%s)"
            params.append(list(doc_ids))
        params += [lit, k]
        sql = QUERY_SQL.format(
            doc_filter=doc_filter
        )  # fixed fragments only, no user data
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
        out: list[ScoredChunk] = []
        for chunk_id, d, text, meta, score in rows:
            if isinstance(meta, str):
                meta = json.loads(meta)
            out.append(
                ScoredChunk(
                    chunk_id=chunk_id,
                    tenant_id=tenant_id,
                    doc_id=d,
                    text=text,
                    metadata=meta or {},
                    score=float(score),
                )
            )
        return out

    async def delete_doc(self, tenant_id, owner_id, doc_id) -> int:
        check_id("tenant_id", tenant_id)
        check_id("owner_id", owner_id)
        check_id("doc_id", doc_id)
        async with self._pool.connection() as conn:
            cur = await conn.execute(DELETE_SQL, (tenant_id, owner_id, doc_id))
            return int(cur.rowcount)

    async def _fetch_chunks(self, where: str, params: list[Any]) -> list[ScoredChunk]:
        sql = (
            "SELECT chunk_id, doc_id, text, metadata FROM vector_chunks "
            "WHERE tenant_id = %s AND owner_id = %s" + where + " ORDER BY doc_id, chunk_id"
        )  # `where` is a fixed fragment, never user data
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
        out: list[ScoredChunk] = []
        for chunk_id, d, text, meta in rows:
            if isinstance(meta, str):
                meta = json.loads(meta)
            out.append(
                ScoredChunk(
                    chunk_id=chunk_id, doc_id=d, text=text, metadata=meta or {}, score=0.0,
                    tenant_id=params[0],
                )
            )
        return out

    async def get_chunks(self, tenant_id, owner_id, chunk_ids) -> list[ScoredChunk]:
        check_id("tenant_id", tenant_id)
        check_id("owner_id", owner_id)
        for c in chunk_ids:
            check_id("chunk_id", c)
        if not chunk_ids:
            return []
        return await self._fetch_chunks(
            " AND chunk_id = ANY(%s)", [tenant_id, owner_id, list(chunk_ids)]
        )

    async def list_chunks(self, tenant_id, owner_id, doc_ids=None) -> list[ScoredChunk]:
        """All stored chunks (score 0.0) for the owner; used to rebuild the BM25 index."""
        check_id("tenant_id", tenant_id)
        check_id("owner_id", owner_id)
        if doc_ids is None:
            return await self._fetch_chunks("", [tenant_id, owner_id])
        for d in doc_ids:
            check_id("doc_id", d)
        if not doc_ids:
            return []
        return await self._fetch_chunks(" AND doc_id = ANY(%s)", [tenant_id, owner_id, list(doc_ids)])

    async def count(self, tenant_id, owner_id=None, doc_id=None) -> int:
        check_id("tenant_id", tenant_id)
        sql = "SELECT count(*) FROM vector_chunks WHERE tenant_id = %s"
        params: list[Any] = [tenant_id]
        if owner_id is not None:
            check_id("owner_id", owner_id)
            sql += " AND owner_id = %s"
            params.append(owner_id)
        if doc_id is not None:
            check_id("doc_id", doc_id)
            sql += " AND doc_id = %s"
            params.append(doc_id)
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            row = await cur.fetchone()
        return int(row[0])


__all__ = ["SCHEMA_SQL", "ChunkRecord", "PgVectorStore"]

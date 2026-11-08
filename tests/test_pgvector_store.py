"""PgVectorStore with a fake pool: SQL shape and bound parameters, no server."""

import pytest
from app.vectorstore import ChunkRecord, PgVectorStore
from app.vectorstore.base import EMBEDDING_DIM


class FakeCursor:
    def __init__(self, conn, rows=None, rowcount=0):
        self.conn, self.rows, self.rowcount = conn, rows or [], rowcount

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def executemany(self, sql, rows):
        self.conn.calls.append(("many", sql, list(rows)))

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows[0]


class FakeConn:
    def __init__(self, rows=None, rowcount=0):
        self.calls, self.rows, self.rowcount = [], rows, rowcount

    def cursor(self):
        return FakeCursor(self)

    async def execute(self, sql, params=None):
        self.calls.append(("one", sql, params))
        return FakeCursor(self, self.rows, self.rowcount)


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def connection(self):
        conn = self.conn

        class CM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False

        return CM()


def emb():
    return [0.5] * EMBEDDING_DIM


async def test_upsert_binds_params():
    conn = FakeConn()
    store = PgVectorStore(FakePool(conn))
    evil = "x'); injected --"
    await store.upsert_chunks(
        "t1",
        "u1",
        "d1",
        [ChunkRecord(chunk_id="a", text=evil, metadata={"k": 1})],
        [emb()],
    )
    kind, sql, rows = conn.calls[0]
    assert kind == "many" and "%s" in sql and evil not in sql
    assert rows[0][:5] == ("t1", "u1", "d1", "a", evil)
    assert rows[0][5] == '{"k": 1}'


async def test_query_scoped_and_parameterised():
    conn = FakeConn(rows=[("a", "d1", "hello", {"p": 1}, 0.9)])
    store = PgVectorStore(FakePool(conn))
    res = await store.query(emb(), 3, "t1", "u1", doc_ids=["d1", "d2"])
    _, sql, params = conn.calls[0]
    assert "tenant_id = %s AND owner_id = %s" in sql
    assert "doc_id = ANY(%s)" in sql and "<=>" in sql
    assert "t1" not in sql and "u1" not in sql
    assert params[1:3] == ["t1", "u1"] and params[3] == ["d1", "d2"] and params[-1] == 3
    assert res[0].score == 0.9 and res[0].metadata == {"p": 1}


async def test_query_without_doc_filter_and_empty_doc_ids():
    conn = FakeConn(rows=[])
    store = PgVectorStore(FakePool(conn))
    await store.query(emb(), 3, "t1", "u1")
    assert "ANY" not in conn.calls[0][1]
    assert await store.query(emb(), 3, "t1", "u1", doc_ids=[]) == []
    assert len(conn.calls) == 1


async def test_delete_and_count():
    conn = FakeConn(rows=[(4,)], rowcount=2)
    store = PgVectorStore(FakePool(conn))
    assert await store.delete_doc("t1", "u1", "d1") == 2
    sql, params = conn.calls[0][1:]
    assert sql.startswith("DELETE") and params == ("t1", "u1", "d1")
    assert await store.count("t1", "u1", "d1") == 4
    sql, params = conn.calls[1][1:]
    assert sql.count("%s") == 3 and params == ["t1", "u1", "d1"]


async def test_rejects_bad_ids_before_sql():
    conn = FakeConn()
    store = PgVectorStore(FakePool(conn))
    with pytest.raises(ValueError):
        await store.delete_doc("t1", "u1", "d1'; --")
    with pytest.raises(ValueError):
        await store.count("t1 OR 1=1")
    assert conn.calls == []

"""VectorStore contract tests. The Pg class below reruns the same cases on pgvector."""

import os

import pytest
from app.vectorstore import ChunkRecord, InMemoryVectorStore
from app.vectorstore.base import EMBEDDING_DIM


def vec(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v


def chunks(*ids: str) -> list[ChunkRecord]:
    return [
        ChunkRecord(chunk_id=i, text=f"text {i}", metadata={"page": 1}) for i in ids
    ]


@pytest.fixture
def store():
    return InMemoryVectorStore()


async def test_add_and_query_top_k(store):
    await store.upsert_chunks(
        "t1", "u1", "d1", chunks("a", "b", "c"), [vec(0), vec(1), vec(2)]
    )
    res = await store.query(vec(1), 2, "t1", "u1")
    assert res[0].chunk_id == "b"
    assert len(res) == 2
    assert res[0].score == pytest.approx(1.0)
    assert res[0].doc_id == "d1" and res[0].metadata == {"page": 1}


async def test_upsert_replaces(store):
    await store.upsert_chunks("t1", "u1", "d1", chunks("a"), [vec(0)])
    await store.upsert_chunks(
        "t1", "u1", "d1", [ChunkRecord(chunk_id="a", text="new")], [vec(0)]
    )
    assert await store.count("t1", "u1", "d1") == 1
    assert (await store.query(vec(0), 1, "t1", "u1"))[0].text == "new"


async def test_delete_by_doc(store):
    await store.upsert_chunks("t1", "u1", "d1", chunks("a", "b"), [vec(0), vec(1)])
    await store.upsert_chunks("t1", "u1", "d2", chunks("c"), [vec(2)])
    assert await store.delete_doc("t1", "u1", "d1") == 2
    assert await store.count("t1") == 1
    assert await store.delete_doc("t1", "u1", "d1") == 0


async def test_tenant_and_owner_isolation(store):
    await store.upsert_chunks("t1", "u1", "d1", chunks("a"), [vec(0)])
    await store.upsert_chunks("t2", "u1", "d1", chunks("a"), [vec(0)])
    await store.upsert_chunks("t1", "u2", "d1", chunks("a"), [vec(0)])
    assert len(await store.query(vec(0), 10, "t1", "u1")) == 1
    assert await store.count("t1") == 2
    assert await store.count("t1", "u1") == 1
    assert await store.delete_doc("t2", "u2", "d1") == 0  # wrong owner deletes nothing
    assert await store.count("t2") == 1


async def test_doc_filter(store):
    await store.upsert_chunks("t1", "u1", "d1", chunks("a"), [vec(0)])
    await store.upsert_chunks("t1", "u1", "d2", chunks("b"), [vec(0)])
    res = await store.query(vec(0), 10, "t1", "u1", doc_ids=["d2"])
    assert [r.doc_id for r in res] == ["d2"]
    assert await store.query(vec(0), 10, "t1", "u1", doc_ids=[]) == []


async def test_validation(store):
    with pytest.raises(ValueError):
        await store.query(vec(0), 5, "", "u1")
    with pytest.raises(ValueError):
        await store.query([0.1], 5, "t1", "u1")
    with pytest.raises(ValueError):
        await store.query(vec(0), 0, "t1", "u1")
    with pytest.raises(ValueError):
        await store.upsert_chunks("t1", "u1", "d/../x", chunks("a"), [vec(0)])
    with pytest.raises(ValueError):
        await store.upsert_chunks("t1", "u1", "d1", chunks("a", "b"), [vec(0)])


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL unset")
class TestPgContract:
    """Same contract on pgvector. LOCAL db only (docker compose)."""

    @pytest.fixture
    async def store(self):
        from app.vectorstore import PgVectorStore
        from app.vectorstore.pgvector_store import SCHEMA_SQL
        from psycopg_pool import AsyncConnectionPool

        url = os.environ["DATABASE_URL"]
        assert any(h in url for h in ("localhost", "127.0.0.1", "@db")), "local DB only"
        async with AsyncConnectionPool(url, min_size=1, max_size=2, open=False) as pool:
            await pool.open()
            async with pool.connection() as conn:
                await conn.execute(SCHEMA_SQL)
                await conn.execute("DELETE FROM vector_chunks")
            yield PgVectorStore(pool)

    test_add_and_query_top_k = test_add_and_query_top_k
    test_upsert_replaces = test_upsert_replaces
    test_delete_by_doc = test_delete_by_doc
    test_tenant_and_owner_isolation = test_tenant_and_owner_isolation
    test_doc_filter = test_doc_filter

"""T-027: ObjectStore local + S3 (fake client), key scheme, original persistence, delete."""

from pathlib import Path

import pytest
from app.core.config import Settings
from app.objectstore import (
    LocalObjectStore,
    ObjectStoreError,
    S3ObjectStore,
    build_object_store,
    original_key,
)
from app.services.ingestion import IngestionService
from app.store.registry import SqliteRegistry
from vecutil import V111, make_store

FIX = Path(__file__).parent / "fixtures"
K = "tenant/t1/d1/a.csv"


async def test_local_roundtrip(tmp_path):
    s = LocalObjectStore(tmp_path / "o")
    assert not await s.exists("t1", K)
    assert await s.get("t1", K) is None
    await s.put("t1", K, b"hello")
    assert await s.exists("t1", K) and await s.get("t1", K) == b"hello"
    await s.delete("t1", K)
    assert not await s.exists("t1", K)
    await s.delete("t1", K)  # idempotent


@pytest.mark.parametrize(
    "key",
    [
        "tenant/t1/../../x",
        "/etc/passwd",
        "tenant/t1/d1/../../../x",
        "tenant/t1//a",
        "tenant/t1/d1/a\\b",
        "tenant/t1/d1/a\x00",
        "tenant/t2/d1/a.csv",
        "a/b/c/d",
        "tenant/t1/..",
    ],
)
async def test_traversal_and_foreign_prefix_rejected(tmp_path, key):
    s = LocalObjectStore(tmp_path / "o")
    for op in (
        s.put("t1", key, b"x"),
        s.get("t1", key),
        s.delete("t1", key),
        s.exists("t1", key),
    ):
        with pytest.raises(ObjectStoreError):
            await op


def test_original_key_scheme_and_sanitising():
    assert original_key("t1", "d1", "sales.csv") == "tenant/t1/d1/sales.csv"
    assert original_key("t1", "d1", "../../a b.csv") == "tenant/t1/d1/a_b.csv"
    with pytest.raises(ObjectStoreError):
        original_key("t/1", "d1", "a.csv")


class FakeClient:
    def __init__(self):
        self.objs: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, str, str]] = []

    def put_object(self, Bucket, Key, Body):
        self.calls.append(("put", Bucket, Key))
        self.objs[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        self.calls.append(("get", Bucket, Key))
        if (Bucket, Key) not in self.objs:
            e = Exception("nope")
            e.response = {"Error": {"Code": "NoSuchKey"}}
            raise e
        import io

        return {"Body": io.BytesIO(self.objs[(Bucket, Key)])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objs:
            e = Exception("nf")
            e.response = {"Error": {"Code": "404"}}
            raise e
        return {}

    def delete_object(self, Bucket, Key):
        self.calls.append(("delete", Bucket, Key))
        self.objs.pop((Bucket, Key), None)


async def test_s3_bucket_and_key():
    c = FakeClient()
    s = S3ObjectStore(c, "bkt")
    key = original_key("t1", "d1", "x.csv")
    await s.put("t1", key, b"abc")
    assert c.calls[0] == ("put", "bkt", "tenant/t1/d1/x.csv")
    assert await s.exists("t1", key) and await s.get("t1", key) == b"abc"
    assert await s.get("t1", "tenant/t1/d1/none.csv") is None
    await s.delete("t1", key)
    assert not await s.exists("t1", key)
    with pytest.raises(ObjectStoreError):
        await s.get("t2", key)


def test_s3_requires_bucket_and_default_is_local(tmp_path):
    with pytest.raises(ObjectStoreError):
        S3ObjectStore(FakeClient(), "")
    st = build_object_store(Settings(object_store_path=str(tmp_path)))
    assert isinstance(st, LocalObjectStore)


@pytest.fixture
def env(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    store = make_store()
    c = FakeClient()
    objs = S3ObjectStore(c, "bkt")
    from app.core.models import FakeEmbedder

    return (
        IngestionService(reg, store.inner, FakeEmbedder(), objects=objs),
        reg,
        store,
        c,
    )


async def test_ingest_csv_stores_original(env):
    svc, _reg, _store, c = env
    doc = await svc.ingest("u1", str(FIX / "sales.csv"), "sales.csv", "text/csv")
    assert doc.status == "ready"
    key = svc.original_key(doc)
    assert key == f"tenant/default/{doc.doc_id}/sales.csv"
    assert c.objs[("bkt", key)] == (FIX / "sales.csv").read_bytes()
    n = len(c.calls)
    await svc.ingest(
        "u1", str(FIX / "sales.csv"), "sales.csv", "text/csv"
    )  # dedupe: no rewrite
    assert len(c.calls) == n


async def test_delete_removes_original(env):
    svc, reg, store, c = env
    doc = await svc.ingest("u1", str(FIX / "sales.csv"), "sales.csv", "text/csv")
    assert store.count("u1", doc.doc_id) > 0 and c.objs
    assert not await svc.delete("u2", doc.doc_id)  # foreign owner: nothing touched
    assert c.objs
    assert await svc.delete("u1", doc.doc_id)
    assert not c.objs
    assert store.count("u1", doc.doc_id) == 0
    assert reg.get("u1", doc.doc_id) is None
    assert store.query("u1", V111, k=5, doc_id=doc.doc_id) == []

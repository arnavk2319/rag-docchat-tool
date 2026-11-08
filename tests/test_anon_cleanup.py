"""T-036: anonymous data TTL purge (fakes only, frozen clock)."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.objectstore import LocalObjectStore, original_key
from app.services.cleanup import purge_expired_anonymous, purge_loop
from app.services.ingestion import IngestionService
from app.store.parents import SqliteParents
from app.store.registry import InMemoryRegistry
from app.store.sessions import InMemorySessions
from app.vectorstore import ChunkRecord, InMemoryVectorStore

NOW = datetime(2026, 1, 10, 12, tzinfo=UTC)
DEMO = "demo"


class Env:
    def __init__(self, tmp_path):
        self.reg = InMemoryRegistry()
        self.vs = InMemoryVectorStore()
        self.obj = LocalObjectStore(tmp_path / "obj")
        self.ing = IngestionService(
            self.reg, self.vs, parents=SqliteParents(tmp_path / "p.db"), objects=self.obj
        )

    async def seed(self, tenant, owner, name, age_h):
        doc_id = self.reg.for_tenant(tenant).create(owner, name, "text/plain", 1, name)
        self.reg._docs[doc_id].created_at = NOW - timedelta(hours=age_h)
        await self.vs.upsert_chunks(
            tenant, owner, doc_id, [ChunkRecord(chunk_id=f"{doc_id}-c", text="t", metadata={})], [[1.0] + [0.0] * 383]
        )
        doc = self.reg.for_tenant(tenant).get(owner, doc_id)
        await self.obj.put(tenant, original_key(tenant, doc_id, name), b"x")
        return doc

    async def alive(self, tenant, doc):
        has_row = self.reg.for_tenant(tenant).get(doc.owner_id, doc.doc_id) is not None
        has_obj = await self.obj.exists(tenant, original_key(tenant, doc.doc_id, doc.filename))
        has_vec = bool(await self.vs.list_chunks(tenant, doc.owner_id, [doc.doc_id]))
        return has_row, has_obj, has_vec


async def run(env, sessions=None, **kw):
    return await purge_expired_anonymous(
        env.ing, sessions, tenant_id=DEMO, ttl_hours=24, now=NOW, **kw
    )


async def test_purges_only_old_anonymous_in_demo(tmp_path):
    e = Env(tmp_path)
    old = await e.seed(DEMO, "anon-aa", "old.txt", 25)
    new = await e.seed(DEMO, "anon-bb", "new.txt", 1)
    real_demo = await e.seed(DEMO, "alice", "alice.txt", 99)
    other = await e.seed("acme", "anon-aa", "acme.txt", 99)
    res = await run(e)
    assert res["documents"] == 1
    assert await e.alive(DEMO, old) == (False, False, False)
    assert await e.alive(DEMO, new) == (True, True, True)
    assert await e.alive(DEMO, real_demo) == (True, True, True)
    assert await e.alive("acme", other) == (True, True, True)


async def test_idempotent_and_bounded(tmp_path):
    e = Env(tmp_path)
    for i in range(3):
        await e.seed(DEMO, f"anon-{i}", f"f{i}.txt", 30)
    assert (await run(e, batch=2))["documents"] == 2
    assert (await run(e, batch=2))["documents"] == 1
    assert await run(e) == {"documents": 0, "failed": 0, "sessions": 0}


async def test_failure_logged_and_continues(tmp_path, caplog):
    e = Env(tmp_path)
    bad = await e.seed(DEMO, "anon-x", "bad.txt", 30)
    good = await e.seed(DEMO, "anon-y", "good.txt", 30)
    real_delete = e.obj.delete

    async def flaky(tenant, key):
        if "bad.txt" in key:
            raise RuntimeError("secret-content")
        await real_delete(tenant, key)

    e.obj.delete = flaky
    with caplog.at_level(logging.INFO):
        res = await run(e)
    assert res["documents"] == 1 and res["failed"] == 1
    assert (await e.alive(DEMO, good))[0] is False
    assert (await e.alive(DEMO, bad))[0] is True  # retried next run
    text = caplog.text
    assert "RuntimeError" in text
    for leak in ("secret-content", "anon-x", "anon-y", "bad.txt", bad.doc_id):
        assert leak not in text


async def test_expired_sessions_purged(tmp_path):
    e = Env(tmp_path)
    clock = [NOW]
    s = InMemorySessions(ttl_hours=24, clock=lambda: clock[0])
    st = s.for_tenant(DEMO)
    st.create("anon-a")
    clock[0] = NOW + timedelta(hours=25)
    assert (await run(e, s))["sessions"] == 1
    assert (await run(e, s))["sessions"] == 0


async def test_loop_survives_errors_and_cancels():
    class Boom:
        ingestion = None

    calls = []

    async def sleep(_):
        calls.append(1)
        if len(calls) == 3:
            raise asyncio.CancelledError
        await asyncio.sleep(0)

    try:
        await purge_loop(Boom(), 1, sleep=sleep)
    except asyncio.CancelledError:
        pass
    assert len(calls) == 3


def test_sqlite_list_older_than_prefix_cutoff_limit(tmp_path):
    from app.store.registry import SqliteRegistry

    r = SqliteRegistry(tmp_path / "r.db")
    ids = {}
    for i, owner in enumerate(["anon-a", "anon-b", "anon-c", "anonXd", "an%", "a_on-z", "alice"]):
        ids[owner] = r.create(owner, f"f{i}", "text/plain", 1, f"s{i}")
    with r._conn() as c:  # age: everything old except anon-c
        c.execute("UPDATE documents SET created_at=?", ((NOW - timedelta(hours=30)).isoformat(),))
        c.execute("UPDATE documents SET created_at=? WHERE owner_id='anon-c'", (NOW.isoformat(),))
    got = r.list_older_than("anon-", NOW - timedelta(hours=24), 10)
    assert {d.owner_id for d in got} == {"anon-a", "anon-b"}
    assert len(r.list_older_than("anon-", NOW - timedelta(hours=24), 1)) == 1
    assert {d.owner_id for d in r.list_older_than("an%", NOW, 10)} == {"an%"}  # no LIKE widening
    assert {d.owner_id for d in r.list_older_than("a_on", NOW, 10)} == {"a_on-z"}


def test_postgres_list_older_than_params():
    from app.store.pg import PostgresRegistry

    calls = []

    class Cur:
        def fetchall(self):
            return []

    class Conn:
        def execute(self, sql, params=()):
            calls.append((sql, params))
            return Cur()

    class Pool:
        def connection(self):
            conn = Conn()

            class CM:
                def __enter__(self):
                    return conn

                def __exit__(self, *a):
                    return False

            return CM()

    PostgresRegistry(Pool()).for_tenant("demo").list_older_than("anon-", NOW, 7)
    sql, params = calls[0]
    assert params == ("demo", 5, "anon-", NOW, 7)
    assert "tenant_id=%s" in sql and "left(owner_id, %s)=%s" in sql and "LIKE" not in sql.upper()
    assert "created_at < %s" in sql and "LIMIT %s" in sql


def test_failing_purge_on_start_does_not_block_startup(monkeypatch):
    from app.core.config import get_settings
    from app.main import create_app
    from fastapi.testclient import TestClient

    class Boom:
        def for_tenant(self, _):
            raise RuntimeError("down")

    monkeypatch.setenv("PURGE_ON_START", "true")
    get_settings.cache_clear()
    app = create_app()
    app.state.ingestion = Boom()
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
    get_settings.cache_clear()

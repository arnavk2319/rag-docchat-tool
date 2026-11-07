"""T-026: Registry contract against the in-memory repo; Postgres repo checked with a recording fake pool."""

import pytest
from app.store.pg import PostgresRegistry
from app.store.registry import InMemoryRegistry, SqliteRegistry


@pytest.fixture(params=["memory", "sqlite"])
def reg(request, tmp_path):
    return InMemoryRegistry() if request.param == "memory" else SqliteRegistry(tmp_path / "r.db")


def test_create_if_absent_dedupes_per_owner(reg):
    d1, c1 = reg.create_if_absent("u1", "a.txt", "text/plain", 3, "sha")
    d2, c2 = reg.create_if_absent("u1", "b.txt", "text/plain", 3, "sha")
    d3, c3 = reg.create_if_absent("u2", "a.txt", "text/plain", 3, "sha")
    assert c1 and not c2 and c3
    assert d2.doc_id == d1.doc_id != d3.doc_id


def test_owner_scoping(reg):
    d, _ = reg.create_if_absent("u1", "a.txt", "text/plain", 3, "sha")
    assert reg.get("u2", d.doc_id) is None and reg.list_by_owner("u2") == []
    assert reg.update_status("u2", d.doc_id, "ready") is False
    assert reg.delete("u2", d.doc_id) is False
    assert reg.get("u1", d.doc_id) is not None


def test_status_and_failure_reason_lifecycle(reg):
    d, _ = reg.create_if_absent("u1", "a.txt", "text/plain", 3, "sha")
    assert reg.update_status("u1", d.doc_id, "failed")
    assert reg.set_failure_reason("u1", d.doc_id, "parse_error")
    assert reg.get("u1", d.doc_id).failure_reason == "parse_error"
    assert reg.update_status("u1", d.doc_id, "processing")  # leaving failed clears the reason
    got = reg.get("u1", d.doc_id)
    assert got.status == "processing" and got.failure_reason is None
    with pytest.raises(ValueError):
        reg.update_status("u1", d.doc_id, "bogus")
    with pytest.raises(ValueError):
        reg.update_pages_or_sheets("u1", d.doc_id, -1)
    assert reg.update_pages_or_sheets("u1", d.doc_id, 4) and reg.get("u1", d.doc_id).pages_or_sheets == 4


def test_list_delete_and_get_by_sha(reg):
    a, _ = reg.create_if_absent("u1", "a.txt", "text/plain", 1, "s1")
    reg.create_if_absent("u1", "b.txt", "text/plain", 1, "s2")
    assert [d.filename for d in reg.list_by_owner("u1")] == ["a.txt", "b.txt"]
    assert reg.get_by_sha256("u1", "s1").doc_id == a.doc_id and reg.get_by_sha256("u2", "s1") is None
    assert reg.delete("u1", a.doc_id) and not reg.delete("u1", a.doc_id)
    assert len(reg.list_by_owner("u1")) == 1


class RecordingPool:
    """Fake psycopg pool: records (sql, params); returns no rows."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def connection(self):
        pool = self

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=()):
                pool.calls.append((sql, tuple(params)))

                class Cur:
                    rowcount = 0

                    def fetchone(self):
                        return None

                    def fetchall(self):
                        return []

                return Cur()

        return Conn()


def test_postgres_registry_filters_every_statement_by_tenant_and_owner():
    pool = RecordingPool()
    r = PostgresRegistry(pool, tenant_id="t9")
    r.get("o1", "d1")
    r.get_by_sha256("o1", "s")
    r.list_by_owner("o1")
    r.delete("o1", "d1")
    r.update_status("o1", "d1", "ready")
    r.update_pages_or_sheets("o1", "d1", 2)
    r.set_failure_reason("o1", "d1", "x")
    assert len(pool.calls) == 7
    for sql, params in pool.calls:
        assert "tenant_id=%s" in sql and "owner_id=%s" in sql, sql
        assert "t9" in params and "o1" in params

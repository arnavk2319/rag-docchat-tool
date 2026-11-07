"""Extra T-005 registry tests: gaps beyond tests/test_registry.py."""

import sqlite3
import threading
from datetime import UTC, timedelta

import pytest
from app.store.registry import Document, Registry, SqliteRegistry
from pydantic import ValidationError

A, B = "user-a", "user-b"


@pytest.fixture
def reg(tmp_path):
    return SqliteRegistry(tmp_path / "x" / "y" / "r.db")


def mk(reg, owner=A, sha="h1", name="a.pdf"):
    return reg.create(owner, name, "application/pdf", 10, sha, 1)


def test_same_sha_different_owners_distinct_ids(reg):
    a, b = mk(reg, A), mk(reg, B)
    assert a != b
    assert reg.get(A, a).owner_id == A and reg.get(B, b).owner_id == B


def test_same_owner_different_sha_distinct_ids(reg):
    assert mk(reg, sha="1") != mk(reg, sha="2")
    assert len(reg.list_by_owner(A)) == 2


def test_delete_then_recreate_gets_fresh_id(reg):
    old = mk(reg)
    assert reg.delete(A, old)
    new = mk(reg)
    assert new != old and reg.get(A, old) is None and reg.get(A, new)


def test_delete_unknown_and_foreign_leaves_row(reg):
    i = mk(reg)
    assert reg.delete(A, "nope") is False
    assert reg.delete(B, i) is False
    assert reg.get(A, i) is not None


def test_update_status_unknown_and_foreign(reg):
    i = mk(reg)
    assert reg.update_status(A, "nope", "ready") is False
    assert reg.update_status(B, i, "ready") is False
    assert reg.get(A, i).status == "pending"


def test_list_deterministic_scoped_and_empty(reg):
    ids = [mk(reg, sha=str(n)) for n in range(5)]
    mk(reg, B, "other")
    first = [d.doc_id for d in reg.list_by_owner(A)]
    assert first == [d.doc_id for d in reg.list_by_owner(A)]
    assert sorted(first) == sorted(ids)
    created = [d.created_at for d in reg.list_by_owner(A)]
    assert created == sorted(created)  # oldest first (created_at, doc_id)
    assert reg.list_by_owner("nobody") == []


def test_unicode_and_long_filename_roundtrip(reg):
    name = "résumé_日本語_😀" + "x" * 5000
    i = reg.create("üser-ñ", name, "m", 1, "ŝha", 0)
    assert reg.get("üser-ñ", i).filename == name


def test_empty_owner_or_sha_rejected_empty_filename_ok(reg):
    with pytest.raises(ValidationError):
        reg.create("", "f", "m", 1, "s", 0)
    with pytest.raises(ValidationError):
        reg.create(A, "f", "m", 1, "", 0)
    assert reg.get(A, reg.create(A, "", "m", 1, "s", 0)).filename == ""
    assert reg.list_by_owner("") == []


@pytest.mark.parametrize("size,pages", [(-1, 0), (1, -1)])
def test_negative_numbers_rejected(reg, size, pages):
    with pytest.raises(ValidationError):
        reg.create(A, "f", "m", size, "s", pages)
    assert reg.list_by_owner(A) == []
    with pytest.raises(ValidationError):
        Document(
            doc_id="d", owner_id=A, filename="f", mime="m", size=size,
            sha256="s", pages_or_sheets=pages, created_at="2024-01-01T00:00:00+00:00",
        )  # fmt: skip


def test_created_at_utc_roundtrip(reg):
    d1 = reg.get(A, mk(reg))
    d2 = reg.get(A, d1.doc_id)
    assert d1.created_at.utcoffset() == timedelta(0)
    assert d1.created_at.tzinfo is not None and d1.created_at == d2.created_at
    assert d1.created_at.astimezone(UTC) == d1.created_at


def test_wal_mode_on(tmp_path):
    p = tmp_path / "w.db"
    SqliteRegistry(p)
    c = sqlite3.connect(p)
    try:
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        c.close()


def test_parent_dir_autocreated(tmp_path):
    p = tmp_path / "a" / "b" / "c" / "r.db"
    SqliteRegistry(p)
    assert p.exists()


def test_two_instances_see_each_other(tmp_path):
    p = tmp_path / "s.db"
    r1, r2 = SqliteRegistry(p), SqliteRegistry(p)
    i = mk(r1)
    assert r2.get(A, i) is not None
    assert r2.delete(A, i) and r1.get(A, i) is None


def test_protocol_satisfied_by_fake_and_real(reg):
    class Fake:
        def __init__(self):
            self.d: dict[str, Document] = {}

        def create(
            self,
            owner_id,
            filename,
            mime,
            size,
            sha256,
            pages_or_sheets=0,
            status="pending",
        ):
            doc = Document(
                doc_id=str(len(self.d)), owner_id=owner_id, filename=filename, mime=mime,
                size=size, sha256=sha256, pages_or_sheets=pages_or_sheets,
                created_at="2024-01-01T00:00:00+00:00", status=status,
            )  # fmt: skip
            self.d[doc.doc_id] = doc
            return doc.doc_id

        def get(self, owner_id, doc_id):
            d = self.d.get(doc_id)
            return d if d and d.owner_id == owner_id else None

        def list_by_owner(self, owner_id):
            return [d for d in self.d.values() if d.owner_id == owner_id]

        def delete(self, owner_id, doc_id):
            return (
                self.d.pop(doc_id, None) is not None
                if self.get(owner_id, doc_id)
                else False
            )

        def update_status(self, owner_id, doc_id, status):
            d = self.get(owner_id, doc_id)
            if d:
                d.status = status
            return d is not None

    def use(r: Registry) -> bool:
        i = r.create(A, "f", "m", 1, "s")
        return (
            r.update_status(A, i, "ready")
            and r.get(A, i).status == "ready"
            and r.delete(A, i)
        )

    assert use(Fake()) and use(reg)


def test_concurrent_50_distinct_creates_8_threads(reg):
    ids, errs, lock = [], [], threading.Lock()
    chunks = [range(n, 50, 8) for n in range(8)]

    def work(rng):
        try:
            for n in rng:
                i = mk(reg, sha=f"sha-{n}")
                with lock:
                    ids.append(i)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=work, args=(c,)) for c in chunks]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)
    assert not errs and len(ids) == 50 and len(set(ids)) == 50
    assert len(reg.list_by_owner(A)) == 50


def test_corrupt_db_file_clear_error(tmp_path):
    p = tmp_path / "bad.db"
    p.write_bytes(b"this is definitely not a sqlite database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        SqliteRegistry(p)

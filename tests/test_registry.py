import sqlite3
import threading

import pytest
from app.store.registry import Document, SqliteRegistry
from pydantic import ValidationError

A, B = "user-a", "user-b"


@pytest.fixture
def reg(tmp_path):
    return SqliteRegistry(tmp_path / "sub" / "r.db")


def mk(reg, owner=A, sha="h1", name="a.pdf"):
    return reg.create(owner, name, "application/pdf", 10, sha, 3)


def test_create_get_fields(reg):
    d = reg.get(A, mk(reg))
    assert isinstance(d, Document)
    assert (d.owner_id, d.filename, d.mime, d.size, d.sha256, d.pages_or_sheets) == (
        A,
        "a.pdf",
        "application/pdf",
        10,
        "h1",
        3,
    )
    assert d.status == "pending" and d.created_at.tzinfo is not None


def test_list_delete_update(reg):
    i1, i2 = mk(reg, sha="x"), mk(reg, sha="y")
    assert {d.doc_id for d in reg.list_by_owner(A)} == {i1, i2}
    assert reg.update_status(A, i1, "ready")
    assert reg.get(A, i1).status == "ready"
    assert reg.delete(A, i1) and reg.get(A, i1) is None
    assert not reg.delete(A, i1)
    assert [d.doc_id for d in reg.list_by_owner(A)] == [i2]


def test_owner_scoping(reg):
    i = mk(reg)
    assert reg.get(B, i) is None
    assert reg.list_by_owner(B) == []
    assert reg.delete(B, i) is False
    assert reg.update_status(B, i, "failed") is False
    assert reg.get(A, i).status == "pending"


def test_dedupe_same_owner_only(reg):
    assert mk(reg) == mk(reg, name="other.pdf")
    assert len(reg.list_by_owner(A)) == 1
    assert mk(reg, owner=B) != mk(reg)


def test_invalid_status_rejected(reg):
    i = mk(reg)
    with pytest.raises(ValueError):
        reg.update_status(A, i, "bogus")
    with pytest.raises(ValidationError):
        reg.create(A, "f", "m", 1, "z", 1, status="bogus")


def test_injection_strings_stored_verbatim(reg):
    evil = "x'); DELETE FROM documents;--"
    i = reg.create(evil, evil, "m", 1, "s'--", 1)
    mk(reg)
    d = reg.get(evil, i)
    assert d.filename == evil and d.owner_id == evil
    assert reg.get("' OR '1'='1", i) is None
    assert reg.list_by_owner("' OR '1'='1") == []
    assert len(reg.list_by_owner(evil)) == 1
    assert len(reg.list_by_owner(A)) == 1


def test_concurrent_same_sha_one_row(reg):
    ids, errs = [], []

    def work():
        try:
            ids.append(mk(reg))
        except sqlite3.Error as e:  # pragma: no cover
            errs.append(e)

    ts = [threading.Thread(target=work) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs and len(set(ids)) == 1
    assert len(reg.list_by_owner(A)) == 1


def test_reopen_keeps_data_and_schema_idempotent(tmp_path):
    p = tmp_path / "d" / "r.db"
    i = mk(SqliteRegistry(p))
    assert SqliteRegistry(p).get(A, i) is not None


def test_default_path_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cfg" / "c.db"))
    r = SqliteRegistry()
    assert (tmp_path / "cfg" / "c.db").exists()
    assert r.list_by_owner(A) == []

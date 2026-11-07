import sqlite3
import threading

import pytest
from app.store.registry import SqliteRegistry

A, B = "user-a", "user-b"


@pytest.fixture
def reg(tmp_path):
    return SqliteRegistry(tmp_path / "r.db")


def test_create_if_absent_flag(reg):
    d1, c1 = reg.create_if_absent(A, "a.pdf", "application/pdf", 1, "h", 1)
    d2, c2 = reg.create_if_absent(
        A, "b.pdf", "application/pdf", 2, "h", 5, status="ready"
    )
    assert c1 is True and c2 is False
    assert d1.doc_id == d2.doc_id
    # dedupe hit does not overwrite the original row
    assert d2.filename == "a.pdf" and d2.status == "pending" and d2.size == 1
    assert reg.get(A, d1.doc_id).filename == "a.pdf"
    _, c3 = reg.create_if_absent(B, "a.pdf", "application/pdf", 1, "h", 1)
    assert c3 is True


def test_create_if_absent_concurrent_exactly_one_created(reg):
    results = []

    def work():
        results.append(reg.create_if_absent(A, "a.pdf", "m", 1, "h", 1))

    ts = [threading.Thread(target=work) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sum(1 for _, created in results if created) == 1
    assert len({d.doc_id for d, _ in results}) == 1


def test_get_by_sha256_owner_scoped(reg):
    d, _ = reg.create_if_absent(A, "a.pdf", "m", 1, "h", 1)
    assert reg.get_by_sha256(A, "h").doc_id == d.doc_id
    assert reg.get_by_sha256(B, "h") is None
    assert reg.get_by_sha256(A, "nope") is None


def test_check_constraint_and_user_version(tmp_path):
    p = tmp_path / "r.db"
    SqliteRegistry(p)
    conn = sqlite3.connect(p)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO documents VALUES ('i','o','f','m',1,'s',1,'t','bogus')"
            )
    finally:
        conn.close()

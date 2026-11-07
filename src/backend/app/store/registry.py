"""SQLite document registry (stdlib sqlite3, WAL, parameterized SQL only).

All calls are synchronous; async callers must wrap them in run_blocking.
A new connection is opened per call, so the registry is safe across threads.
Every method is owner-scoped: a foreign owner sees None / [] / False.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, get_args

from pydantic import BaseModel, Field

from app.store.sqlite import connect, init_db

Status = Literal["pending", "processing", "ready", "failed"]
_STATUSES = frozenset(get_args(Status))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    pages_or_sheets INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','processing','ready','failed')),
    failure_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_owner_sha ON documents(owner_id, sha256);
"""

SCHEMA_VERSION = 2


def _migrate(c: sqlite3.Connection) -> None:
    """Idempotent: v1 -> v2 adds documents.failure_reason (nullable); safe to re-run/race."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(documents)")}
    if "failure_reason" not in cols:
        try:
            c.execute("ALTER TABLE documents ADD COLUMN failure_reason TEXT")
        except sqlite3.OperationalError as e:  # another process migrated first
            if "duplicate column" not in str(e):
                raise
    if c.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

_INSERT_COLS = "doc_id, owner_id, filename, mime, size, sha256, pages_or_sheets, created_at, status"
_COLS = _INSERT_COLS + ", failure_reason"


class Document(BaseModel):
    doc_id: str
    owner_id: str = Field(min_length=1)
    filename: str
    mime: str
    size: int = Field(ge=0)
    sha256: str = Field(min_length=1)
    pages_or_sheets: int = Field(ge=0)
    created_at: datetime
    status: Status = "pending"
    failure_reason: str | None = None  # short code (parse_error, ...), never document text


class Registry(Protocol):
    def create(
        self,
        owner_id: str,
        filename: str,
        mime: str,
        size: int,
        sha256: str,
        pages_or_sheets: int = 0,
        status: Status = "pending",
    ) -> str: ...
    def create_if_absent(
        self,
        owner_id: str,
        filename: str,
        mime: str,
        size: int,
        sha256: str,
        pages_or_sheets: int = 0,
        status: Status = "pending",
    ) -> tuple[Document, bool]: ...
    def get_by_sha256(self, owner_id: str, sha256: str) -> Document | None: ...
    def get(self, owner_id: str, doc_id: str) -> Document | None: ...
    def list_by_owner(self, owner_id: str) -> list[Document]: ...
    def delete(self, owner_id: str, doc_id: str) -> bool: ...
    def update_status(self, owner_id: str, doc_id: str, status: Status) -> bool: ...
    def update_pages_or_sheets(self, owner_id: str, doc_id: str, n: int) -> bool: ...
    def set_failure_reason(self, owner_id: str, doc_id: str, reason: str | None) -> bool: ...


class SqliteRegistry:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from app.core.config import get_settings

            db_path = get_settings().db_path
        self._path = Path(db_path)
        init_db(self._path, _SCHEMA)
        with self._conn() as c:
            _migrate(c)

    @property
    def db_path(self) -> Path:
        return self._path

    def _conn(self) -> AbstractContextManager[sqlite3.Connection]:
        return connect(self._path)

    @staticmethod
    def _row(row: tuple) -> Document:
        return Document(
            doc_id=row[0],
            owner_id=row[1],
            filename=row[2],
            mime=row[3],
            size=row[4],
            sha256=row[5],
            pages_or_sheets=row[6],
            created_at=row[7],
            status=row[8],
            failure_reason=row[9],
        )

    def create(
        self,
        owner_id: str,
        filename: str,
        mime: str,
        size: int,
        sha256: str,
        pages_or_sheets: int = 0,
        status: Status = "pending",
    ) -> str:
        doc, _ = self.create_if_absent(
            owner_id, filename, mime, size, sha256, pages_or_sheets, status
        )
        return doc.doc_id

    def create_if_absent(
        self,
        owner_id: str,
        filename: str,
        mime: str,
        size: int,
        sha256: str,
        pages_or_sheets: int = 0,
        status: Status = "pending",
    ) -> tuple[Document, bool]:
        """Return (document, created). created is True only if this call inserted the row."""
        doc = Document(
            doc_id=uuid.uuid4().hex,
            owner_id=owner_id,
            filename=filename,
            mime=mime,
            size=size,
            sha256=sha256,
            pages_or_sheets=pages_or_sheets,
            created_at=datetime.now(UTC),
            status=status,
        )
        with self._conn() as c:
            # Unique (owner_id, sha256) makes dedupe atomic across threads/processes.
            cur = c.execute(
                f"INSERT OR IGNORE INTO documents ({_INSERT_COLS}) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    doc.doc_id,
                    doc.owner_id,
                    doc.filename,
                    doc.mime,
                    doc.size,
                    doc.sha256,
                    doc.pages_or_sheets,
                    doc.created_at.isoformat(),
                    doc.status,
                ),
            )
            created = cur.rowcount == 1
            row = c.execute(
                f"SELECT {_COLS} FROM documents WHERE owner_id=? AND sha256=?",
                (owner_id, sha256),
            ).fetchone()
        return self._row(row), created

    def get_by_sha256(self, owner_id: str, sha256: str) -> Document | None:
        with self._conn() as c:
            row = c.execute(
                f"SELECT {_COLS} FROM documents WHERE owner_id=? AND sha256=?",
                (owner_id, sha256),
            ).fetchone()
        return self._row(row) if row else None

    def get(self, owner_id: str, doc_id: str) -> Document | None:
        with self._conn() as c:
            row = c.execute(
                f"SELECT {_COLS} FROM documents WHERE owner_id=? AND doc_id=?",
                (owner_id, doc_id),
            ).fetchone()
        return self._row(row) if row else None

    def list_by_owner(self, owner_id: str) -> list[Document]:
        with self._conn() as c:
            rows = c.execute(
                f"SELECT {_COLS} FROM documents WHERE owner_id=? ORDER BY created_at, doc_id",
                (owner_id,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, owner_id: str, doc_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM documents WHERE owner_id=? AND doc_id=?",
                (owner_id, doc_id),
            )
            return cur.rowcount > 0

    def update_status(self, owner_id: str, doc_id: str, status: Status) -> bool:
        if status not in _STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        with self._conn() as c:
            cur = c.execute(
                "UPDATE documents SET status=?, "
                "failure_reason=CASE WHEN ?='failed' THEN failure_reason ELSE NULL END "
                "WHERE owner_id=? AND doc_id=?",
                (status, status, owner_id, doc_id),
            )
            return cur.rowcount > 0

    def update_pages_or_sheets(self, owner_id: str, doc_id: str, n: int) -> bool:
        if n < 0:
            raise ValueError("pages_or_sheets must be >= 0")
        with self._conn() as c:
            cur = c.execute(
                "UPDATE documents SET pages_or_sheets=? WHERE owner_id=? AND doc_id=?",
                (n, owner_id, doc_id),
            )
            return cur.rowcount > 0

    def set_failure_reason(self, owner_id: str, doc_id: str, reason: str | None) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE documents SET failure_reason=? WHERE owner_id=? AND doc_id=?",
                (reason, owner_id, doc_id),
            )
            return cur.rowcount > 0


class InMemoryRegistry:
    """Dict-backed Registry (same contract as SqliteRegistry); used by unit tests."""

    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}
        self._lock = threading.Lock()

    def create(
        self, owner_id, filename, mime, size, sha256, pages_or_sheets=0, status="pending"
    ) -> str:
        return self.create_if_absent(
            owner_id, filename, mime, size, sha256, pages_or_sheets, status
        )[0].doc_id

    def create_if_absent(
        self, owner_id, filename, mime, size, sha256, pages_or_sheets=0, status="pending"
    ) -> tuple[Document, bool]:
        doc = Document(
            doc_id=uuid.uuid4().hex, owner_id=owner_id, filename=filename, mime=mime, size=size,
            sha256=sha256, pages_or_sheets=pages_or_sheets, created_at=datetime.now(UTC),
            status=status,
        )
        with self._lock:
            for d in self._docs.values():
                if d.owner_id == owner_id and d.sha256 == sha256:
                    return d.model_copy(), False
            self._docs[doc.doc_id] = doc
        return doc.model_copy(), True

    def get_by_sha256(self, owner_id: str, sha256: str) -> Document | None:
        with self._lock:
            for d in self._docs.values():
                if d.owner_id == owner_id and d.sha256 == sha256:
                    return d.model_copy()
        return None

    def get(self, owner_id: str, doc_id: str) -> Document | None:
        with self._lock:
            d = self._docs.get(doc_id)
            return d.model_copy() if d and d.owner_id == owner_id else None

    def list_by_owner(self, owner_id: str) -> list[Document]:
        with self._lock:
            docs = [d.model_copy() for d in self._docs.values() if d.owner_id == owner_id]
        return sorted(docs, key=lambda d: (d.created_at, d.doc_id))

    def delete(self, owner_id: str, doc_id: str) -> bool:
        with self._lock:
            d = self._docs.get(doc_id)
            if d is None or d.owner_id != owner_id:
                return False
            del self._docs[doc_id]
            return True

    def _update(self, owner_id: str, doc_id: str, **fields) -> bool:
        with self._lock:
            d = self._docs.get(doc_id)
            if d is None or d.owner_id != owner_id:
                return False
            self._docs[doc_id] = d.model_copy(update=fields)
            return True

    def update_status(self, owner_id: str, doc_id: str, status: Status) -> bool:
        if status not in _STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        d = self.get(owner_id, doc_id)
        keep = d.failure_reason if status == "failed" and d else None
        return self._update(owner_id, doc_id, status=status, failure_reason=keep)

    def update_pages_or_sheets(self, owner_id: str, doc_id: str, n: int) -> bool:
        if n < 0:
            raise ValueError("pages_or_sheets must be >= 0")
        return self._update(owner_id, doc_id, pages_or_sheets=n)

    def set_failure_reason(self, owner_id: str, doc_id: str, reason: str | None) -> bool:
        return self._update(owner_id, doc_id, failure_reason=reason)

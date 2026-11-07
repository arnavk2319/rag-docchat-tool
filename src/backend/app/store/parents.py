"""Owner-scoped parent chunks (context returned to the LLM) in the registry's SQLite file.

Children are embedded in the VectorStore; their parent text lives here. Stdlib sqlite3, per-call
connections, parameterized SQL only. Every read/write is keyed by owner_id.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path

from pydantic import BaseModel

from app.store.sqlite import connect, init_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parents (
    owner_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    page INTEGER,
    sheet TEXT,
    section TEXT NOT NULL DEFAULT '',
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (owner_id, parent_id)
);
CREATE INDEX IF NOT EXISTS idx_parents_owner_doc ON parents(owner_id, doc_id);
CREATE INDEX IF NOT EXISTS idx_parents_owner_parent ON parents(owner_id, parent_id);
"""
_COLS = "owner_id, doc_id, parent_id, page, sheet, section, char_start, char_end, text"
_SQLITE_VARS = 500


class Parent(BaseModel):
    owner_id: str
    doc_id: str
    parent_id: str
    page: int | None = None
    sheet: str | None = None
    section: str = ""
    char_start: int
    char_end: int
    text: str


class SqliteParents:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from app.core.config import get_settings

            db_path = get_settings().db_path
        self._path = Path(db_path)
        init_db(self._path, _SCHEMA)

    def _conn(self) -> AbstractContextManager[sqlite3.Connection]:
        return connect(self._path)

    def replace_doc(self, owner_id: str, doc_id: str, parents: Sequence[Parent]) -> None:
        """Atomically replace all parents of a document (one transaction)."""
        if not owner_id:
            raise ValueError("owner_id is required")
        rows = []
        for p in parents:
            if p.owner_id != owner_id or p.doc_id != doc_id:
                raise ValueError("parent owner/doc mismatch")
            rows.append(
                (p.owner_id, p.doc_id, p.parent_id, p.page, p.sheet, p.section,
                 p.char_start, p.char_end, p.text)
            )
        with self._conn() as c:
            c.execute("DELETE FROM parents WHERE owner_id = ? AND doc_id = ?", (owner_id, doc_id))
            c.executemany(f"INSERT INTO parents ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?)", rows)

    def delete_doc(self, owner_id: str, doc_id: str) -> None:
        if not owner_id:
            raise ValueError("owner_id is required")
        with self._conn() as c:
            c.execute("DELETE FROM parents WHERE owner_id = ? AND doc_id = ?", (owner_id, doc_id))

    def get_parents(self, owner_id: str, parent_ids: Sequence[str]) -> list[Parent]:
        """Parents in the requested order; unknown or foreign-owner ids are skipped."""
        if not owner_id:
            raise ValueError("owner_id is required")
        found: dict[str, Parent] = {}
        ids = list(dict.fromkeys(parent_ids))
        with self._conn() as c:
            for i in range(0, len(ids), _SQLITE_VARS):
                part = ids[i : i + _SQLITE_VARS]
                marks = ",".join("?" * len(part))
                cur = c.execute(
                    f"SELECT {_COLS} FROM parents WHERE owner_id = ? AND parent_id IN ({marks})",
                    (owner_id, *part),
                )
                for r in cur:
                    found[r[2]] = Parent(
                        owner_id=r[0], doc_id=r[1], parent_id=r[2], page=r[3], sheet=r[4],
                        section=r[5], char_start=r[6], char_end=r[7], text=r[8],
                    )
        return [found[i] for i in ids if i in found]

    def list_doc_parents(self, owner_id: str, doc_id: str) -> list[Parent]:
        """All parents of one document in document order, owner-scoped.

        Order (explicit): page ascending (NULL = non-paged sheets/sections first), then sheet
        name, then char_start, then parent_id as the final tie-break."""
        if not owner_id:
            raise ValueError("owner_id is required")
        with self._conn() as c:
            rows = c.execute(
                f"SELECT {_COLS} FROM parents WHERE owner_id = ? AND doc_id = ? "
                "ORDER BY (page IS NULL) DESC, page ASC, sheet ASC, char_start ASC, parent_id ASC",
                (owner_id, doc_id),
            ).fetchall()
        return [
            Parent(owner_id=r[0], doc_id=r[1], parent_id=r[2], page=r[3], sheet=r[4],
                   section=r[5], char_start=r[6], char_end=r[7], text=r[8])
            for r in rows
        ]

    def count(self, owner_id: str, doc_id: str | None = None) -> int:
        sql, args = "SELECT COUNT(*) FROM parents WHERE owner_id = ?", [owner_id]
        if doc_id is not None:
            sql, args = sql + " AND doc_id = ?", [owner_id, doc_id]
        with self._conn() as c:
            return int(c.execute(sql, args).fetchone()[0])

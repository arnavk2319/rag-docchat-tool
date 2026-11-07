"""Shared SQLite helpers for the registry and parents stores (same DB file)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Per-call connection (30 s busy timeout); commits on success, rolls back on error."""
    conn = sqlite3.connect(path, timeout=30)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(path: Path, schema: str) -> None:
    """Create the parent dir, enable WAL and apply an idempotent schema script."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(schema)

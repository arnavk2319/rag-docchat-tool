"""Per-owner daily LLM spend in the SQLite registry DB (UTC day, USD)."""

from __future__ import annotations

from pathlib import Path

from .sqlite import connect, init_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_spend (
    owner_id TEXT NOT NULL,
    day TEXT NOT NULL,
    usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (owner_id, day)
);
"""


class SqliteSpend:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        init_db(self._path, _SCHEMA)  # CREATE IF NOT EXISTS: safe on an existing registry DB

    def spent(self, owner_id: str, day: str) -> float:
        with connect(self._path) as c:
            row = c.execute(
                "SELECT usd FROM daily_spend WHERE owner_id=? AND day=?", (owner_id, day)
            ).fetchone()
        return float(row[0]) if row else 0.0

    def add(self, owner_id: str, day: str, usd: float) -> None:
        if usd <= 0:
            return
        with connect(self._path) as c:
            c.execute(
                "INSERT INTO daily_spend(owner_id, day, usd) VALUES (?,?,?) "
                "ON CONFLICT(owner_id, day) DO UPDATE SET usd = usd + excluded.usd",
                (owner_id, day, usd),
            )
            # keep the table small: drop rows older than 40 days
            c.execute("DELETE FROM daily_spend WHERE day < date(?, '-40 day')", (day,))

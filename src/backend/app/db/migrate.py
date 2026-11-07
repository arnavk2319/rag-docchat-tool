"""Apply migrations/*.sql in order (ADR-0003 s8). Local databases only.

Usage: DATABASE_URL=postgresql://... python -m app.db.migrate
Refuses any host other than localhost/127.0.0.1/db unless a human sets MIGRATE_ALLOW_REMOTE=1.
Applied names + sha256 checksums are recorded in schema_migrations; a changed file is an error.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "db"})
MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "migrations"
LOCK_KEY = 726_001  # arbitrary constant for pg_advisory_lock


class MigrationError(Exception):
    pass


def assert_local(url: str, allow_remote: bool = False) -> None:
    if not url.startswith(("postgresql://", "postgres://")):
        raise MigrationError("DATABASE_URL must be a postgresql:// URL")
    host = urlparse(url).hostname or ""
    if host not in LOCAL_HOSTS and not allow_remote:
        raise MigrationError("refusing to migrate a non-local database host")


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, str, str]]:
    """(name, sql, sha256) sorted by name."""
    out = []
    for f in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        sql = f.read_text(encoding="utf-8")
        out.append((f.name, sql, hashlib.sha256(sql.encode()).hexdigest()))
    return out


def migrate(url: str, directory: Path = MIGRATIONS_DIR, *, allow_remote: bool = False) -> list[str]:
    assert_local(url, allow_remote)
    import psycopg

    applied_now: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "name text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = dict(conn.execute("SELECT name, checksum FROM schema_migrations").fetchall())
            for name, sql, checksum in list_migrations(directory):
                if name in done:
                    if done[name] != checksum:
                        raise MigrationError(f"checksum mismatch for applied migration {name}")
                    continue
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)", (name, checksum)
                    )
                applied_now.append(name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    return applied_now


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    try:
        done = migrate(url, allow_remote=os.environ.get("MIGRATE_ALLOW_REMOTE") == "1")
    except MigrationError as e:
        sys.stderr.write("migrate: " + str(e) + "\n")
        return 1
    sys.stdout.write("applied: " + (", ".join(done) or "nothing to do") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

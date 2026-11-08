"""T-026: migration runner refuses non-local hosts; SQL files are ordered and additive. No database."""

from pathlib import Path

import pytest
from app.db import migrate as m


@pytest.mark.parametrize("url", ["postgresql://u:p@localhost/db", "postgres://u@127.0.0.1:5432/x", "postgresql://u@db/x"])
def test_local_hosts_allowed(url):
    m.assert_local(url)


@pytest.mark.parametrize("url", ["postgresql://u:p@prod.example.com/db", "postgresql://u@10.0.0.5/x", "", "mysql://u@localhost/x"])
def test_remote_or_invalid_refused(url):
    with pytest.raises(m.MigrationError):
        m.assert_local(url)


def test_remote_needs_explicit_human_override():
    m.assert_local("postgresql://u@prod.example.com/x", allow_remote=True)


def test_migrate_refuses_before_connecting():
    with pytest.raises(m.MigrationError):
        m.migrate("postgresql://u@prod.example.com/x")


def test_migration_files_ordered_with_checksums():
    ms = m.list_migrations()
    assert ms and ms[0][0] == "0001_init.sql" and len(ms[0][2]) == 64
    assert [n for n, *_ in ms] == sorted(n for n, *_ in ms)
    sql = ms[0][1]
    for table in ("vector_chunks", "documents", "sessions", "session_messages"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "tenant_id" in sql and "UNIQUE (tenant_id, owner_id, sha256)" in sql


def test_migrations_dir_is_in_repo():
    assert (Path(m.MIGRATIONS_DIR) / "0001_init.sql").exists()

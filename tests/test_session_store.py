"""T-026: SessionStore contract against the in-memory repo; Postgres repo scoping via a fake pool."""

from datetime import UTC, datetime, timedelta

import pytest
from app.store.pg import PostgresSessions
from app.store.sessions import InMemorySessions, SessionLimitError, SqliteSessions
from test_registry_store import RecordingPool


@pytest.fixture(params=["memory", "sqlite"])
def make(request, tmp_path):
    def f(**kw):
        return InMemorySessions(**kw) if request.param == "memory" else SqliteSessions(tmp_path / "s.db", **kw)

    return f


def test_create_append_read_and_owner_isolation(make):
    s = make()
    sid = s.create("u1")
    assert s.messages("u1", sid) == [] and s.exists("u1", sid)
    assert s.append_turn("u1", sid, "q?", "a.", ["d1"])
    msgs = s.messages("u1", sid)
    assert [(m.role, m.content, m.citation_ids) for m in msgs] == [("user", "q?", []), ("assistant", "a.", ["d1"])]
    assert s.messages("u2", sid) is None and not s.exists("u2", sid)
    assert s.append_turn("u2", sid, "q", "a", []) is False
    assert s.delete("u2", sid) is False and s.delete("u1", sid) is True
    assert s.messages("u1", sid) is None


def test_message_bound_drops_oldest_whole_turn(make):
    s = make(max_messages=4, max_tokens=10_000)
    sid = s.create("u")
    for i in range(5):
        s.append_turn("u", sid, f"q{i}", f"a{i}", [])
    assert [m.content for m in s.messages("u", sid)] == ["q3", "a3", "q4", "a4"]


def test_token_bound_keeps_newest_turn(make):
    s = make(max_messages=20, max_tokens=100)
    sid = s.create("u")
    for i in range(4):
        s.append_turn("u", sid, "x" * 300, "y" * 300, [])
    assert len(s.messages("u", sid)) == 2


def test_per_owner_cap(make):
    s = make()
    for _ in range(2):
        s.create("u", max_per_owner=2)
    with pytest.raises(SessionLimitError):
        s.create("u", max_per_owner=2)
    s.create("other", max_per_owner=2)


def test_in_memory_ttl_expiry_and_renewal():
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    s = InMemorySessions(ttl_hours=1, clock=lambda: now[0])
    sid = s.create("u")
    now[0] += timedelta(minutes=59)
    assert s.append_turn("u", sid, "q", "a", [])  # renews the idle timer
    now[0] += timedelta(minutes=59)
    assert s.exists("u", sid)
    now[0] += timedelta(minutes=2)
    assert not s.exists("u", sid) and s.messages("u", sid) is None
    assert s.purge_expired() == 1


def test_postgres_sessions_filter_by_tenant_and_owner():
    pool = RecordingPool()
    s = PostgresSessions(pool, tenant_id="t9")
    pool.calls.clear()
    s.exists("o1", "s1")
    s.messages("o1", "s1")
    s.append_turn("o1", "s1", "q", "a", [])
    s.delete("o1", "s1")
    assert pool.calls
    for sql, params in pool.calls:
        assert "tenant_id=%s" in sql and "t9" in params, sql
        if "pg_advisory" not in sql:
            assert "owner_id=%s" in sql and "o1" in params, sql

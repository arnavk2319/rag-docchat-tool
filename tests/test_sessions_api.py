"""T-035: GET /v1/sessions list + messages (owner/tenant isolation, bounds, title sanitizing)."""

from datetime import UTC, datetime, timedelta

import pytest
from app.api.sessions import make_title
from app.core.security import sanitize_output
from app.main import app
from app.store.pg import PostgresSessions
from app.store.sessions import InMemorySessions, SqliteSessions
from authutil import bearer
from fastapi.testclient import TestClient
from test_registry_store import RecordingPool

A, B = bearer("alice"), bearer("bob")


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        c.app.state.sessions = InMemorySessions()
        yield c


def mk(c, q=None, h=A, owner="alice"):
    sid = c.post("/v1/sessions", headers=h).json()["session_id"]
    if q is not None:
        c.app.state.sessions.append_turn(owner, sid, q, "ans", [])
    return sid


def test_list_orders_titles_and_paginates(client):
    s1 = mk(client, "first " + "x" * 200)
    s2 = mk(client, "second")
    s3 = mk(client)  # empty session
    r = client.get("/v1/sessions", headers=A)
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 3
    assert {i["session_id"] for i in j["items"]} == {s1, s2, s3}
    by = {i["session_id"]: i for i in j["items"]}
    assert by[s3]["title"] == "New chat" and by[s2]["title"] == "second"
    assert len(by[s1]["title"]) == 80 and "updated_at" in by[s1]
    r = client.get("/v1/sessions?limit=1&offset=1", headers=A).json()
    assert len(r["items"]) == 1 and r["total"] == 3


def test_list_is_owner_scoped_and_requires_auth(client):
    mk(client, "mine")
    assert client.get("/v1/sessions", headers=B).json() == {"items": [], "total": 0}
    assert client.get("/v1/sessions").status_code == 401


@pytest.mark.parametrize(
    "q", ["limit=0", "limit=101", "limit=-1", "offset=-1", "limit=x"]
)
def test_list_bounds_422(client, q):
    assert client.get(f"/v1/sessions?{q}", headers=A).status_code == 422


def test_title_is_sanitized():
    t = make_title("<script>alert(1)</script> [x](http://evil.test)")
    assert "<" not in t and "evil.test" not in t


def test_get_messages_and_isolation(client):
    sid = mk(client, "hi")
    r = client.get(f"/v1/sessions/{sid}", headers=A).json()
    assert [m["role"] for m in r["messages"]] == ["user", "assistant"]
    assert r["messages"][0]["created_at"]
    x = client.get(f"/v1/sessions/{sid}", headers=B)
    y = client.get("/v1/sessions/nope", headers=B)
    assert x.status_code == y.status_code == 404 and x.json() == y.json()
    assert set(x.json()) == {"detail", "code"}


def test_tenant_view_hides_other_tenant():
    s = InMemorySessions()
    sid = s.for_tenant("t1").create("u")
    assert s.for_tenant("t2").list_sessions("u", 10, 0) == ([], 0)
    assert s.for_tenant("t2").messages("u", sid) is None
    assert s.for_tenant("t1").list_sessions("u", 10, 0)[1] == 1


def test_sqlite_list(tmp_path):
    s = SqliteSessions(tmp_path / "s.db")
    a = s.create("u")
    s.create("u")
    s.append_turn("u", a, "qa", "x", [])
    rows, total = s.list_sessions("u", 10, 0)
    assert total == 2 and rows[0].session_id == a and rows[0].first_question == "qa"
    assert s.list_sessions("v", 10, 0) == ([], 0)


def test_postgres_list_scoped():
    pool = RecordingPool()

    class Conn(pool.connection().__class__):
        def execute(self, sql, params=()):
            cur = super().execute(sql, params)
            cur.fetchone = lambda: (0,)
            return cur

    pool.connection = lambda: Conn()
    s = PostgresSessions(pool, tenant_id="t9")
    pool.calls.clear()
    assert s.list_sessions("o1", 5, 10) == ([], 0)
    main = [(q, p) for q, p in pool.calls if "ORDER BY" in q]
    assert len(main) == 1 and len(pool.calls) >= 2
    sql, params = main[0]
    assert "s.tenant_id=%s" in sql and "s.owner_id=%s" in sql and "s.expires_at > now()" in sql
    assert "ORDER BY s.updated_at DESC" in sql and "LIMIT %s OFFSET %s" in sql
    assert "t9" in params and "o1" in params and params[-2:] == (5, 10)


def test_title_cap_with_escaping_expansion():
    assert len(make_title("&" * 80)) <= 80


def test_get_messages_escaped_like_stream(client):
    sid = mk(client, "<script>x</script>")
    msgs = client.get(f"/v1/sessions/{sid}", headers=A).json()["messages"]
    assert msgs[0]["content"] == sanitize_output("<script>x</script>")
    assert "<script>" not in msgs[0]["content"]


def test_expired_excluded_and_newest_first_distinct_stamps():
    t = [datetime(2026, 1, 1, tzinfo=UTC)]
    s = InMemorySessions(clock=lambda: t[0], ttl_hours=1)
    a = s.create("u")
    t[0] += timedelta(minutes=1)
    b = s.create("u")
    t[0] += timedelta(minutes=1)
    s.append_turn("u", a, "q", "a", [])  # a becomes newest
    assert [r.session_id for r in s.list_sessions("u", 10, 0)[0]] == [a, b]
    t[0] += timedelta(minutes=59)  # b expired (created +1m, ttl 1h), a still live
    rows, total = s.list_sessions("u", 10, 0)
    assert total == 1 and rows[0].session_id == a


def test_sqlite_newest_first(tmp_path):
    s = SqliteSessions(tmp_path / "s.db")
    a = s.create("u")
    b = s.create("u")
    s.append_turn("u", a, "q", "a", [])
    assert [r.session_id for r in s.list_sessions("u", 10, 0)[0]] == [a, b]


def test_limit_100_ok_101_422(client):
    assert client.get("/v1/sessions?limit=100", headers=A).status_code == 200
    assert client.get("/v1/sessions?limit=101", headers=A).status_code == 422

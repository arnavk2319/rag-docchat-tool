"""T-022: rate limit, daily spend cap (reserve + reconcile), session cap."""

import sqlite3
import threading
from datetime import UTC, datetime

import pytest
from app.core import llm
from app.core.limits import RateLimiter, SpendCapError, SpendLedger
from app.main import app
from app.store.spend import SqliteSpend
from authutil import bearer
from fastapi.testclient import TestClient

A = bearer("alice")
B = bearer("bob")
J = llm.mock_script


def make_client(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    from app.core import config

    config.get_settings.cache_clear()
    return TestClient(app, raise_server_exceptions=False)


def doc(c, h=A):
    r = c.post("/v1/documents", files=[("files", ("a.txt", b"The sky is blue."))], headers=h)
    return r.json()["results"][0]["doc_id"]


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


# ---- limiter unit ----
def test_limiter_window_fake_clock_and_isolation():
    clk = Clock()
    rl = RateLimiter(3, clock=clk)
    ds = [rl.hit("a") for _ in range(4)]
    assert [d.allowed for d in ds] == [True, True, True, False]
    assert ds[0].remaining == 2 and ds[3].remaining == 0
    assert ds[3].headers()["Retry-After"] == "60"
    assert rl.hit("b").allowed
    clk.t += 30
    d = rl.hit("a")
    assert not d.allowed and d.reset_s == 30
    clk.t += 31
    assert rl.hit("a").allowed


def test_limiter_memory_bounded_and_idle_evicted():
    clk = Clock()
    rl = RateLimiter(5, clock=clk, max_users=50)
    for i in range(500):
        rl.hit(f"u{i}")
    assert len(rl) <= 50
    clk.t += 120
    rl.hit("x")
    assert len(rl) == 1


# ---- ledger unit ----
def test_ledger_reserve_blocks_concurrent_and_reconciles(tmp_path):
    store = SqliteSpend(tmp_path / "d.db")
    led = SpendLedger(store, cap_usd=0.10)
    a = led.acquire("u", 0.06)
    b = led.acquire("u", 0.06)  # 0.06 held < cap, allowed (overshoot bounded by one run)
    assert a and b
    with pytest.raises(SpendCapError):
        led.acquire("u", 0.06)  # held 0.12 >= cap
    assert led.acquire("other", 0.06)
    a.finish(0.01)
    a.finish(5.0)  # idempotent
    assert store.spent("u", datetime.now(UTC).strftime("%Y-%m-%d")) == pytest.approx(0.01)
    assert led.acquire("u", 0.06)  # 0.01 + 0.06 held < cap
    assert led.reserved("u") == pytest.approx(0.12)


def test_ledger_utc_day_rollover_and_inflight_lease(tmp_path):
    store = SqliteSpend(tmp_path / "d.db")
    now = [datetime(2026, 1, 1, 23, 59, tzinfo=UTC)]
    led = SpendLedger(store, 0.05, now=lambda: now[0])
    led.acquire("u", 0.1).finish(0.1)
    with pytest.raises(SpendCapError):
        led.acquire("u", 0.1)
    now[0] = datetime(2026, 1, 2, 0, 1, tzinfo=UTC)
    inflight = led.acquire("u", 0.1)
    now[0] = datetime(2026, 1, 3, 0, 1, tzinfo=UTC)
    inflight.finish(0.02)  # straddling run is billed to the day it settles
    assert store.spent("u", "2026-01-03") == pytest.approx(0.02)
    assert store.spent("u", "2026-01-02") == 0


def test_cap_must_be_positive(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        SpendLedger(SqliteSpend(tmp_path / "d.db"), 0)
    monkeypatch.setenv("DAILY_USD_CAP", "0")
    from app.core.config import Settings
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        Settings()


def test_global_cap_across_users(tmp_path):
    led = SpendLedger(SqliteSpend(tmp_path / "d.db"), 1.0, global_cap_usd=0.10)
    led.acquire("a", 0.06).finish(0.06)
    led.acquire("b", 0.06).finish(0.06)
    with pytest.raises(SpendCapError) as e:
        led.acquire("c", 0.01)
    assert e.value.scope == "global" and led.reserved("c") == 0


def test_reservation_ttl_expiry(tmp_path):
    clk = Clock()
    led = SpendLedger(SqliteSpend(tmp_path / "d.db"), 0.05, clock=clk)
    led.acquire("u", 0.05)  # leaked
    with pytest.raises(SpendCapError):
        led.acquire("u", 0.05)
    clk.t += 901
    assert led.acquire("u", 0.05)


def test_ledger_threads_cannot_all_pass(tmp_path):
    led = SpendLedger(SqliteSpend(tmp_path / "d.db"), cap_usd=0.05)
    got: list[object] = []

    def go() -> None:
        try:
            got.append(led.acquire("u", 0.05))
        except SpendCapError:
            got.append(None)

    ts = [threading.Thread(target=go) for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for g in got if g) == 1  # others raised


def test_spend_table_migration_is_additive(tmp_path):
    p = tmp_path / "old.db"
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE documents (x)")
    SqliteSpend(p)
    SqliteSpend(p)
    with sqlite3.connect(p) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"documents", "daily_spend"} <= names


# ---- API ----
def test_rate_limit_on_query_and_summarize(monkeypatch):
    with make_client(monkeypatch, RATE_LIMIT_PER_MIN=2) as c:
        d = doc(c)
        r1 = c.post("/v1/query", json={"question": "sky?"}, headers=A)
        assert r1.status_code == 200
        assert r1.headers["x-ratelimit-limit"] == "2" and r1.headers["x-ratelimit-remaining"] == "1"
        assert c.post("/v1/summarize", json={"doc_id": d}, headers=A).status_code == 200
        r3 = c.post("/v1/query", json={"question": "sky?", "stream": True}, headers=A)
        assert r3.status_code == 429 and r3.json() == {
            "detail": "Too many requests; retry later", "code": "rate_limited"}
        assert int(r3.headers["retry-after"]) >= 1 and r3.headers["x-ratelimit-remaining"] == "0"
        assert "text/event-stream" not in r3.headers["content-type"]
        assert c.post("/v1/summarize", json={"doc_id": d}, headers=A).status_code == 429
        assert c.post("/v1/query", json={"question": "q"}, headers=B).status_code == 200


def test_stream_success_carries_rate_headers(monkeypatch):
    with make_client(monkeypatch) as c:
        doc(c)
        r = c.post("/v1/query", json={"question": "sky?", "stream": True}, headers=A)
        assert r.status_code == 200 and r.headers["x-ratelimit-limit"] == "30"


def test_daily_cap_blocks_before_llm_call(monkeypatch):
    with make_client(monkeypatch, DAILY_USD_CAP=0.0000001) as c:
        did = doc(c)
        r = c.post("/v1/query", json={"question": "sky?"}, headers=A)
        assert r.status_code == 200
        spent = c.app.state.ledger._store.spent("alice", datetime.now(UTC).strftime("%Y-%m-%d"))
        assert spent > 0  # recorded from actual usage
        calls = []
        real = llm.complete

        async def spy(*a, **k):
            calls.append(1)
            return await real(*a, **k)

        monkeypatch.setattr(llm, "complete", spy)
        for body in ({"question": "q"}, {"question": "q", "stream": True}):
            r = c.post("/v1/query", json=body, headers=A)
            assert r.status_code == 429 and r.json()["code"] == "spend_cap_exceeded"
            assert "text/event-stream" not in r.headers["content-type"]
            assert "x-ratelimit-limit" in r.headers
        r = c.post("/v1/summarize", json={"doc_id": did}, headers=A)
        assert r.status_code == 429 and r.json()["code"] == "spend_cap_exceeded"
        assert calls == []
        assert c.post("/v1/query", json={"question": "q"}, headers=B).status_code == 200  # per-user


def test_stream_records_spend_and_releases(monkeypatch):
    with make_client(monkeypatch) as c:
        doc(c)
        r = c.post("/v1/query", json={"question": "sky?", "stream": True}, headers=A)
        assert r.status_code == 200 and '"usd":0.0}' not in r.text
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        assert c.app.state.ledger._store.spent("alice", day) > 0
        assert c.app.state.ledger.reserved("alice") == 0


def test_cancelled_stream_records_partial_and_releases(monkeypatch, tmp_path):
    import asyncio

    from app.api import sse

    store = SqliteSpend(tmp_path / "d.db")
    led = SpendLedger(store, 1.0)

    class Req:
        async def is_disconnected(self):
            return True

    async def fake_run(question, ctx, *, budget_out, **kw):
        b = llm.Budget(5, 1000, 1.0)
        b.charge(10, 0.03)
        budget_out.append(b)
        await asyncio.sleep(30)

    monkeypatch.setattr(sse, "run_agent", fake_run)
    monkeypatch.setattr(sse, "POLL_S", 0.01)

    async def go():
        lease = led.acquire("u", 0.5)
        frames = [f async for f in sse.stream_agent(Req(), "q", None, {"d1": "a.txt"}, lease=lease)]
        return frames

    assert asyncio.run(go()) == []  # disconnected before final
    assert store.spent("u", datetime.now(UTC).strftime("%Y-%m-%d")) == pytest.approx(0.03)
    assert led.reserved("u") == 0


def test_session_cap(monkeypatch):
    with make_client(monkeypatch, SESSION_MAX_PER_OWNER=2) as c:
        assert c.post("/v1/sessions", headers=A).status_code == 201
        sid = c.post("/v1/sessions", headers=A).json()["session_id"]
        r = c.post("/v1/sessions", headers=A)
        assert r.status_code == 429 and r.json()["code"] == "session_limit_exceeded"
        assert c.post("/v1/sessions", headers=B).status_code == 201
        assert c.delete(f"/v1/sessions/{sid}", headers=A).status_code == 204
        assert c.post("/v1/sessions", headers=A).status_code == 201


def test_limiter_exact_boundary_and_fractional_retry_after():
    clk = Clock()
    rl = RateLimiter(2, clock=clk)
    assert rl.hit("a").allowed and rl.hit("a").allowed
    clk.t += 59.9
    d = rl.hit("a")
    assert not d.allowed and d.reset_s == 1 and d.headers()["Retry-After"] == "1"
    clk.t += 0.1  # t == window
    assert rl.hit("a").allowed


def test_limiter_threaded():
    rl = RateLimiter(10)
    out: list[bool] = []
    ts = [threading.Thread(target=lambda: out.append(rl.hit("u").allowed)) for _ in range(40)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(out) == 10


def test_flood_of_ids_does_not_reset_victim_window():
    clk = Clock()
    rl = RateLimiter(1, clock=clk, max_users=100)
    assert rl.hit("victim").allowed
    assert not rl.hit("victim").allowed
    res = [rl.hit(f"x{i}").allowed for i in range(101)]
    assert res.count(False) >= 1 and len(rl) <= 100  # new ids refused when full
    assert not rl.hit("victim").allowed  # window intact
    d = rl.hit("brand-new")
    assert not d.allowed and d.reset_s >= 1
    clk.t += 61  # idle entries swept -> new ids admitted again
    assert rl.hit("brand-new").allowed


def reserved(c, user):
    return c.app.state.ledger.reserved(user)


def test_422_and_404_leave_no_reservation(monkeypatch):
    with make_client(monkeypatch) as c:
        assert c.post("/v1/query", json={}, headers=A).status_code == 422
        assert c.post("/v1/query", json={"question": "q", "session_id": "nope"}, headers=A).status_code == 404
        assert c.post("/v1/query", json={"question": "q", "doc_ids": ["nope"]}, headers=A).status_code == 404
        assert c.post("/v1/summarize", json={"doc_id": "nope"}, headers=A).status_code == 404
        assert c.post("/v1/summarize", json={}, headers=A).status_code == 422
        assert reserved(c, "alice") == 0 and reserved(c, "*") == 0


def test_llm_failures_release_lease_uncharged(monkeypatch):
    with make_client(monkeypatch) as c:
        did = doc(c)
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        J(llm.LLMTransportError(400, "x"))
        r = c.post("/v1/query", json={"question": "sky?"}, headers=A)
        assert r.status_code == 502
        J(llm.LLMError("boom"))
        c.post("/v1/query", json={"question": "sky?"}, headers=A)
        J(llm.LLMTransportError(400, "x"))
        assert c.post("/v1/summarize", json={"doc_id": did}, headers=A).status_code == 502
        assert reserved(c, "alice") == 0 and reserved(c, "*") == 0
        assert c.app.state.ledger._store.spent("alice", day) == 0


def test_429_headers_on_stream_and_summarize(monkeypatch):
    with make_client(monkeypatch, RATE_LIMIT_PER_MIN=1) as c:
        d = doc(c)
        assert c.post("/v1/query", json={"question": "q"}, headers=A).status_code == 200
        for path, body in (("/v1/query", {"question": "q", "stream": True}), ("/v1/summarize", {"doc_id": d})):
            r = c.post(path, json=body, headers=A)
            assert r.status_code == 429 and r.json()["code"] == "rate_limited"
            assert int(r.headers["retry-after"]) >= 1
            assert {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"} <= set(r.headers)


def test_global_rate_limit_and_spend_codes(monkeypatch):
    with make_client(monkeypatch, GLOBAL_RATE_LIMIT_PER_MIN=2) as c:
        doc(c)  # uploads are not limited
        assert c.post("/v1/query", json={"question": "q"}, headers=A).status_code == 200
        assert c.post("/v1/query", json={"question": "q"}, headers=B).status_code == 200
        r = c.post("/v1/query", json={"question": "q"}, headers=bearer("carol"))
        assert r.status_code == 429 and r.json()["code"] == "global_rate_limited"
        assert int(r.headers["retry-after"]) >= 1


def test_global_spend_cap_code(monkeypatch):
    with make_client(monkeypatch, GLOBAL_DAILY_USD_CAP=0.0000001) as c:
        doc(c)
        assert c.post("/v1/query", json={"question": "sky?"}, headers=A).status_code == 200
        r = c.post("/v1/query", json={"question": "q", "stream": True}, headers=B)
        assert r.status_code == 429 and r.json()["code"] == "global_spend_cap_exceeded"
        assert "text/event-stream" not in r.headers["content-type"]

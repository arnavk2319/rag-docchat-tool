"""T-029: per-tenant daily USD cap on top of per-user caps (fake clock, no real time)."""

from datetime import UTC, datetime, timedelta

import pytest
from app.core import llm
from app.core.limits import SpendCapError, SpendLedger
from app.main import app
from app.store.spend import SqliteSpend
from authutil import bearer
from fastapi.testclient import TestClient


class Day:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def ledger(tmp_path, day, **kw):
    return SpendLedger(SqliteSpend(tmp_path / "s.db"), kw.pop("cap", 1.0), now=day, **kw)


def spend(led, user, tenant, usd):
    led.acquire(user, usd, tenant).finish(usd)


def test_tenant_cap_spans_users_and_other_tenants_unaffected(tmp_path):
    led = ledger(tmp_path, Day(), tenant_cap_usd=0.10)
    spend(led, "u1", "ta", 0.06)
    spend(led, "u2", "ta", 0.06)  # each user is under their own cap; the tenant total is 0.12
    with pytest.raises(SpendCapError) as e:
        led.acquire("u3", 0.01, "ta")
    assert e.value.scope == "tenant"
    led.acquire("u1", 0.01, "tb").finish(0.0)  # same owner id, other tenant: separate user and tenant


def test_per_user_cap_still_applies_inside_tenant_cap(tmp_path):
    led = ledger(tmp_path, Day(), cap=0.05, tenant_cap_usd=10.0)
    spend(led, "u1", "ta", 0.06)
    with pytest.raises(SpendCapError) as e:
        led.acquire("u1", 0.01, "ta")
    assert e.value.scope == "user"
    led.acquire("u2", 0.01, "ta").finish(0.0)
    led.acquire("u1", 0.01, "tb").finish(0.0)  # user key is tenant-namespaced


def test_inflight_reservations_count_against_tenant_cap(tmp_path):
    led = ledger(tmp_path, Day(), tenant_cap_usd=0.10)
    lease = led.acquire("u1", 0.10, "ta")
    with pytest.raises(SpendCapError):
        led.acquire("u2", 0.01, "ta")
    lease.finish(0.0)
    led.acquire("u2", 0.01, "ta").finish(0.0)


def test_cap_resets_next_day_with_fake_clock(tmp_path):
    day = Day()
    led = ledger(tmp_path, day, tenant_cap_usd=0.10)
    spend(led, "u1", "ta", 0.11)
    with pytest.raises(SpendCapError):
        led.acquire("u2", 0.01, "ta")
    day.now += timedelta(days=1)
    led.acquire("u2", 0.01, "ta").finish(0.0)


def test_tenant_cap_must_be_positive(tmp_path):
    with pytest.raises(ValueError):
        ledger(tmp_path, Day(), tenant_cap_usd=0)


def test_api_returns_429_with_clear_message(monkeypatch):
    from app.core import config

    monkeypatch.setenv("TENANT_DAILY_USD_CAP", "0.0000001")
    config.get_settings.cache_clear()
    llm.mock_script()
    other = bearer("alice")  # tenant "default"
    from app.core.auth import mint_token
    from authutil import TEST_SECRET

    tb = {"Authorization": f"Bearer {mint_token('alice', 'tb', TEST_SECRET)}"}
    with TestClient(app, raise_server_exceptions=False) as c:
        for h in (other, tb):
            c.post("/v1/documents", files=[("files", ("a.txt", b"The sky is blue."))], headers=h)
        assert c.post("/v1/query", json={"question": "sky?"}, headers=other).status_code == 200
        r = c.post("/v1/query", json={"question": "sky?"}, headers=bearer("bob"))  # same tenant
        assert r.status_code == 429 and r.json()["code"] == "tenant_spend_cap_exceeded"
        assert "organization" in r.json()["detail"]
        assert c.post("/v1/query", json={"question": "sky?"}, headers=tb).status_code == 200

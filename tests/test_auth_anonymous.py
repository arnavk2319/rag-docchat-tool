"""T-034: POST /v1/auth/anonymous, distinct 401 codes, demo-tenant caps (no live services)."""

import logging
import time

import pytest
from app.core import auth
from app.core.config import Settings, get_settings
from app.core.limits import SpendCapError, SpendLedger
from app.main import create_app
from app.store.spend import SqliteSpend
from authutil import TEST_SECRET, bearer
from fastapi.testclient import TestClient
from pydantic import ValidationError

URL = "/v1/auth/anonymous"


def make(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    get_settings.cache_clear()
    app = create_app()
    get_settings.cache_clear()
    return app


@pytest.fixture
def client(monkeypatch):
    with TestClient(make(monkeypatch, ANON_MINT_RATE_PER_HOUR=3)) as c:
        yield c


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_mint_shape_distinct_owner_demo_tenant(client):
    a, b = client.post(URL).json(), client.post(URL).json()
    assert set(a) == {"access_token", "token_type", "expires_at"} and a["token_type"] == "bearer"
    pa, pb = (auth.verify_token(x["access_token"], TEST_SECRET) for x in (a, b))
    assert pa.owner_id != pb.owner_id and pa.tenant_id == pb.tenant_id == "demo"
    assert client.get("/v1/documents", headers=hdr(a["access_token"])).status_code == 200


def test_ttl_from_env_and_expiry(monkeypatch):
    with TestClient(make(monkeypatch, ANON_TOKEN_TTL_SECONDS=120)) as c:
        r = c.post(URL).json()
        assert abs(r["expires_at"] - (int(time.time()) + 120)) <= 2
        with pytest.raises(auth.TokenExpired):
            auth.verify_token(r["access_token"], TEST_SECRET, now=r["expires_at"])
        monkeypatch.setattr(time, "time", lambda: r["expires_at"] + 1)
        e = c.get("/v1/documents", headers=hdr(r["access_token"]))
        assert e.status_code == 401 and e.json()["code"] == "token_expired"


def test_default_ttl_is_one_hour():
    assert Settings().anon_token_ttl_seconds == 3600


def test_rate_limit_per_ip_socket_peer(client):
    for _ in range(3):
        assert client.post(URL).status_code == 200
    r = client.post(URL, headers={"X-Forwarded-For": "9.9.9.9"})  # ignored: no TRUSTED_PROXY
    assert r.status_code == 429 and r.json()["code"] == "rate_limited" and "Retry-After" in r.headers


def test_trusted_proxy_header_used_only_when_set(monkeypatch):
    app = make(monkeypatch, ANON_MINT_RATE_PER_HOUR=1, TRUSTED_PROXY="X-Forwarded-For")
    with TestClient(app) as c:
        assert c.post(URL, headers={"X-Forwarded-For": "6.6.6.6, 1.1.1.1"}).status_code == 200
        assert c.post(URL, headers={"X-Forwarded-For": "7.7.7.7, 1.1.1.1"}).status_code == 429
        assert c.post(URL, headers={"X-Forwarded-For": "7.7.7.7, 2.2.2.2"}).status_code == 200
        assert c.post(URL, headers={"X-Forwarded-For": "garbage"}).status_code == 200  # peer
        assert c.post(URL).status_code == 429  # peer already used


def test_demo_tenant_never_default():
    for bad in ("default", "DEFAULT", "a b", ""):
        with pytest.raises(ValidationError):
            Settings(demo_tenant_id=bad)
    assert Settings().demo_tenant_id == "demo"


def test_demo_caps(tmp_path):
    led = SpendLedger(
        SqliteSpend(tmp_path / "s.db"), 5.0, tenant_cap_usd=50.0, tenant_caps={"demo": (0.10, 0.15)}
    )
    led.acquire("anon-1", 0.08, "demo").finish(0.08)
    first = led.acquire("anon-1", 0.05, "demo")  # 0.08 spent + 0.05 held: still under 0.10 at check time
    with pytest.raises(SpendCapError) as e:
        led.acquire("anon-1", 0.05, "demo")  # now spent + held >= 0.10
    assert e.value.scope == "user"
    first.finish(0.0)
    led.acquire("anon-2", 0.08, "demo").finish(0.08)
    with pytest.raises(SpendCapError) as e:
        led.acquire("anon-3", 0.01, "demo")
    assert e.value.scope == "tenant"
    led.acquire("anon-1", 1.0, "real").finish(1.0)  # other tenants keep the normal caps


def test_demo_user_cap_via_api(monkeypatch):
    app = make(monkeypatch, ANON_USER_DAILY_USD_CAP=0.001, AGENT_MAX_USD=0.05)
    with TestClient(app) as c:
        tok = c.post(URL).json()["access_token"]
        app.state.ledger.acquire("someone", 0.0005, "demo").finish(0.0015)  # fill another owner
        led = app.state.ledger
        assert led.tenant_caps["demo"][0] == 0.001
        led.acquire("anon-z", 0.0, "demo").finish(0.002)  # anon-z over its 0.001 cap
        with pytest.raises(SpendCapError):
            led.acquire("anon-z", 0.0, "demo")
        # an owner from the minted token hits the 429 via the real endpoint
        owner = auth.verify_token(tok, TEST_SECRET).owner_id
        led.acquire(owner, 0.0, "demo").finish(0.002)
        r = c.post("/v1/query", json={"question": "hi"}, headers=hdr(tok))
        assert r.status_code == 429 and r.json()["code"] == "spend_cap_exceeded"


def test_401_codes(client):
    S = TEST_SECRET
    expired = auth.mint_token("a1", "demo", S, ttl_s=-5)
    good = auth.mint_token("a1", "demo", S)
    cases = {
        "token_expired": [expired],
        "invalid_token": [good[:-3] + "xyz", "garbage", "v1.a.b", auth.mint_token("a1", "demo", "other-secret-0123456789-abcdefghijkl")],
    }
    for code, toks in cases.items():
        for t in toks:
            r = client.get("/v1/documents", headers=hdr(t))
            assert r.status_code == 401 and set(r.json()) == {"detail", "code"}, t
            assert r.json()["code"] == code, t
    # expired but forged signature is NOT reported as expired (no oracle)
    forged = expired[:-3] + "xyz"
    assert client.get("/v1/documents", headers=hdr(forged)).json()["code"] == "invalid_token"
    assert client.get("/v1/documents").json()["code"] == "unauthorized"


def test_minted_tokens_isolated(client):
    a = client.post(URL).json()["access_token"]
    b = client.post(URL).json()["access_token"]
    files = {"files": ("n.txt", b"alpha beta gamma delta " * 20, "text/plain")}
    up = client.post("/v1/documents", files=files, headers=hdr(a))
    assert up.status_code == 200, up.text
    la, lb = (client.get("/v1/documents", headers=hdr(t)).json() for t in (a, b))
    assert "n.txt" in str(la) and "n.txt" not in str(lb)
    # a regular tenant user does not see the demo tenant's documents either
    assert "n.txt" not in str(client.get("/v1/documents", headers=bearer("anon-x")).json())


def test_no_token_in_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    tok = client.post(URL).json()["access_token"]
    client.get("/v1/documents", headers=hdr(tok))
    client.get("/v1/documents", headers=hdr(tok[:-2] + "zz"))
    assert tok not in caplog.text and tok.split(".")[1] not in caplog.text


def test_ip_bucket_normalisation(monkeypatch):
    app = make(monkeypatch, ANON_MINT_RATE_PER_HOUR=1, TRUSTED_PROXY="X-Real-IP")
    with TestClient(app) as c:
        def mint(ip):
            return c.post(URL, headers={"X-Real-IP": ip}).status_code
        assert mint("2001:db8:1:2::1") == 200
        assert mint("2001:db8:1:2:ffff::9") == 429  # same /64
        assert mint("2001:db8:1:3::1") == 200  # different /64
        assert mint("::ffff:1.2.3.4") == 200
        assert mint("1.2.3.4") == 429  # mapped and plain share a bucket


def test_demo_tenant_reserved_ids_rejected():
    for bad in ("public", "system", "admin"):
        with pytest.raises(ValidationError):
            Settings(demo_tenant_id=bad)

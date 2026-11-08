"""T-028: bearer user tokens and tenant API keys (in-memory key store, no DB)."""

import logging

import pytest
from app.api.deps import get_key_store
from app.core import auth
from app.core.auth import InMemoryKeyStore
from app.main import app
from authutil import TEST_SECRET, bearer
from fastapi.testclient import TestClient

S = TEST_SECRET


@pytest.fixture
def client():
    store = InMemoryKeyStore()
    app.dependency_overrides[get_key_store] = lambda: store
    with TestClient(app) as c:
        c.store = store
        yield c
    app.dependency_overrides.pop(get_key_store, None)


def hdr(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def test_valid_token_gives_identity(client):
    assert client.get("/v1/documents", headers=bearer("alice")).status_code == 200
    p = auth.verify_token(auth.mint_token("alice", "t1", S), S)
    assert (p.owner_id, p.tenant_id) == ("alice", "t1")


def test_expired_token_401(client):
    tok = auth.mint_token("alice", "t1", S, ttl_s=10, now=1000)
    with pytest.raises(auth.AuthError):
        auth.verify_token(tok, S, now=1010)
    old = auth.mint_token("alice", "t1", S, ttl_s=-5)
    r = client.get("/v1/documents", headers=hdr(old))
    assert r.status_code == 401 and r.json()["code"] == "token_expired"


def test_tampered_and_wrong_secret_401(client):
    tok = auth.mint_token("alice", "t1", S)
    v, body, sig = tok.split(".")
    other = auth.mint_token("mallory", "t1", S).split(".")[1]
    for bad in (f"{v}.{other}.{sig}", f"{v}.{body}.{sig[:-2]}AA", f"x.{body}.{sig}", "junk", "a.b.c", tok + "x"):
        assert client.get("/v1/documents", headers=hdr(bad)).status_code == 401
    forged = auth.mint_token("alice", "t1", "another-secret-that-is-long-enough-0123456")
    assert client.get("/v1/documents", headers=hdr(forged)).status_code == 401


def test_missing_credentials_401(client):
    r = client.get("/v1/documents")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert r.headers["www-authenticate"] == "Bearer"


def test_x_user_id_no_longer_grants_identity(client):
    assert client.get("/v1/documents", headers={"X-User-Id": "alice"}).status_code == 401


def test_unconfigured_secret_rejects_everything(client, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "")
    from app.core import config

    config.get_settings.cache_clear()
    assert client.get("/v1/documents", headers=bearer("alice")).status_code == 401


def test_api_key_hash_only_and_auth(client, caplog):
    caplog.set_level(logging.DEBUG)
    raw = auth.issue_api_key(client.store, "t1", S)
    _, key_id, secret = raw.split("_", 2)
    rec = client.store.get(key_id)
    assert rec.tenant_id == "t1" and rec.key_hash != secret
    assert secret not in repr(rec) and raw not in repr(client.store.__dict__)
    h = hdr(raw, **{"X-On-Behalf-Of": "svc-user"})
    assert client.get("/v1/documents", headers=h).status_code == 200
    assert secret not in caplog.text and raw not in caplog.text
    assert client.store.get(key_id).last_used_at is not None


def test_api_key_requires_on_behalf_of_and_valid_key(client):
    raw = auth.issue_api_key(client.store, "t1", S)
    assert client.get("/v1/documents", headers=hdr(raw)).status_code == 401
    assert client.get("/v1/documents", headers=hdr(raw, **{"X-On-Behalf-Of": "bad user"})).status_code == 401
    key_id = raw.split("_", 2)[1]
    wrong = f"dck_{key_id}_AAAA"
    assert client.get("/v1/documents", headers=hdr(wrong, **{"X-On-Behalf-Of": "u"})).status_code == 401
    assert client.get("/v1/documents", headers=hdr("dck_nope_x", **{"X-On-Behalf-Of": "u"})).status_code == 401


def test_rotate_old_key_stops_working(client):
    old = auth.issue_api_key(client.store, "t1", S)
    new = auth.rotate_api_key(client.store, old.split("_", 2)[1], S)
    obo = {"X-On-Behalf-Of": "u"}
    assert client.get("/v1/documents", headers=hdr(old, **obo)).status_code == 401
    assert client.get("/v1/documents", headers=hdr(new, **obo)).status_code == 200


def test_revoke_and_expiry_401(client):
    raw = auth.issue_api_key(client.store, "t1", S)
    obo = {"X-On-Behalf-Of": "u"}
    assert client.get("/v1/documents", headers=hdr(raw, **obo)).status_code == 200
    client.store.revoke(raw.split("_", 2)[1])
    assert client.get("/v1/documents", headers=hdr(raw, **obo)).status_code == 401
    exp = auth.issue_api_key(client.store, "t1", S, expires_at=1.0)
    assert client.get("/v1/documents", headers=hdr(exp, **obo)).status_code == 401

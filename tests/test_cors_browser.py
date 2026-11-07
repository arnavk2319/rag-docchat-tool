"""T-032: CORS allow-list, preflight and SSE headers for browser clients."""

import pytest
from app.core.config import get_settings
from app.main import create_app
from authutil import bearer
from fastapi.testclient import TestClient

OK, BAD = "https://app.example.com", "https://evil.example.com"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", f"{OK}/, http://localhost:3000")
    get_settings.cache_clear()
    app = create_app()
    get_settings.cache_clear()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def pre(c, origin):
    return c.options("/v1/documents", headers={
        "Origin": origin, "Access-Control-Request-Method": "DELETE",
        "Access-Control-Request-Headers": "Authorization, Content-Type"})


def test_preflight_allowed(client):
    r = pre(client, OK)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == OK
    assert "authorization" in r.headers["access-control-allow-headers"].lower()
    assert "content-type" in r.headers["access-control-allow-headers"].lower()
    assert "DELETE" in r.headers["access-control-allow-methods"]
    assert "access-control-max-age" in r.headers
    assert "access-control-allow-credentials" not in r.headers


def test_disallowed_origin(client):
    assert "access-control-allow-origin" not in pre(client, BAD).headers
    r = client.get("/health", headers={"Origin": BAD})
    assert "access-control-allow-origin" not in r.headers
    assert client.get("/health", headers={"Origin": OK}).headers["access-control-allow-origin"] == OK


def test_no_wildcard(monkeypatch):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "*")
    get_settings.cache_clear()
    c = TestClient(create_app())
    get_settings.cache_clear()
    assert "access-control-allow-origin" not in c.get("/health", headers={"Origin": OK}).headers


def test_sse_headers_cross_origin(client):
    h = {**bearer("alice"), "Origin": OK}
    d = client.post("/v1/documents", files=[("files", ("a.txt", b"The sky is blue."))], headers=h)
    assert d.status_code == 200
    r = client.post("/v1/query", json={"question": "sky?", "stream": True}, headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    assert r.headers["access-control-allow-origin"] == OK

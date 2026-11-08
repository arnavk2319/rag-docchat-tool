"""T-032: /v1 surface, /health, docs flag, openapi drift."""

import json
import sys
from pathlib import Path

import pytest
from app.core.config import get_settings
from app.main import app, create_app
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def test_business_routes_versioned():
    paths = {r.path for r in app.routes if hasattr(r, "methods")}
    public = {"/health", "/healthz", "/ping-checkpoint", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
    assert all(p.startswith("/v1/") for p in paths - public)
    c = TestClient(app)
    for old in ("/documents", "/query", "/sessions"):
        assert c.get(old).status_code == 404


def test_health_open_and_minimal():
    c = TestClient(app)  # no lifespan: no DB, models or LLM touched
    r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def _app(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    try:
        return create_app()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("flag,code", [("false", 404), ("true", 200)])
def test_docs_flag(monkeypatch, flag, code):
    c = TestClient(_app(monkeypatch, ENABLE_DOCS=flag))
    assert [c.get(p).status_code for p in ("/docs", "/redoc", "/openapi.json")] == [code] * 3


def test_healthz_trimmed_when_docs_off(monkeypatch):
    monkeypatch.setenv("ENABLE_DOCS", "false")
    get_settings.cache_clear()
    try:
        assert TestClient(app).get("/healthz").json() == {"status": "ok"}
    finally:
        get_settings.cache_clear()


def test_openapi_no_drift():
    import export_openapi

    assert (ROOT / "docs" / "openapi.json").read_text() == export_openapi.render()


def test_openapi_content():
    spec = json.loads((ROOT / "docs" / "openapi.json").read_text())
    assert "text/event-stream" in json.dumps(spec["paths"]["/v1/query"]["post"])
    assert {"detail", "code"} <= set(spec["components"]["schemas"]["ErrorBody"]["properties"])

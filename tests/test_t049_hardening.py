"""T-049: upload limiter/quotas, ingest gate, global mint limit, safe failure reasons, headers,
body limits (real app, fake models, mock LLM; no paid API)."""

import asyncio
import io
import logging
from datetime import UTC, datetime

import pytest
from app.api import uploads
from app.core.auth import mint_token
from app.core.config import get_settings
from app.main import create_app
from app.store.registry import Document
from authutil import TEST_SECRET, bearer
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile


def make(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    get_settings.cache_clear()
    app = create_app()
    get_settings.cache_clear()
    return app


def hdr(owner, tenant="default"):
    return {"Authorization": f"Bearer {mint_token(owner, tenant, TEST_SECRET)}"}


def up(c, headers, name="a.txt", body=b"hello world text"):
    return c.post("/v1/documents", files={"files": (name, body)}, headers=headers)


def test_upload_rate_limit_per_user(monkeypatch):
    with TestClient(make(monkeypatch, UPLOAD_RATE_PER_HOUR=2)) as c:
        h = hdr("alice")
        assert [up(c, h, body=b"a%d" % i).status_code for i in range(2)] == [200, 200]
        r = up(c, h, body=b"zzz")
        assert r.status_code == 429 and r.json()["code"] == "rate_limited"
        assert int(r.headers["Retry-After"]) >= 1
        assert up(c, hdr("bob")).status_code == 200  # other user unaffected
        assert up(c, hdr("alice", "other")).status_code == 200  # same id, other tenant


def test_anon_doc_cap_and_non_anon_unaffected(monkeypatch):
    with TestClient(make(monkeypatch, ANON_MAX_DOCS=2)) as c:
        anon = hdr("anon-abc", "demo")
        for i in range(2):
            assert up(c, anon, body=b"doc %d" % i).json()["results"][0]["ok"]
        res = up(c, anon, body=b"doc 3").json()["results"][0]
        assert not res["ok"] and res["error"]["code"] == "doc_limit_reached"
        again = up(c, anon, body=b"doc 0").json()["results"][0]  # same bytes: exempt
        assert again["ok"] and again["deduplicated"]
        for i in range(6):  # non-anon owner is not held to ANON_MAX_DOCS
            assert up(c, hdr("alice"), body=b"d%d" % i).json()["results"][0]["ok"]


def test_owner_doc_cap_setting(monkeypatch):
    with TestClient(make(monkeypatch, OWNER_MAX_DOCS=1)) as c:
        assert up(c, hdr("al"), body=b"one").json()["results"][0]["ok"]
        res = up(c, hdr("al"), body=b"two").json()["results"][0]
        assert res["error"]["code"] == "doc_limit_reached"


def test_demo_tenant_byte_cap(monkeypatch):
    with TestClient(make(monkeypatch, DEMO_TENANT_MAX_BYTES=100, ANON_MAX_DOCS=50)) as c:
        assert up(c, hdr("anon-a", "demo"), body=b"x" * 60).json()["results"][0]["ok"]
        res = up(c, hdr("anon-b", "demo"), body=b"y" * 60).json()["results"][0]  # other owner, same tenant
        assert not res["ok"] and res["error"]["code"] == "storage_limit_reached"
        assert up(c, hdr("anon-b", "demo"), body=b"y" * 40).json()["results"][0]["ok"]
        assert up(c, hdr("carol", "default"), body=b"z" * 500).json()["results"][0]["ok"]  # other tenant


def test_ingest_concurrency_gate():
    live = peak = 0

    class Slow:
        async def ingest(self, owner, path, name, mime, size=None):
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await asyncio.sleep(0.02)
            live -= 1
            return Document(doc_id="d", owner_id=owner, filename=name, mime=mime, size=1,
                            sha256="x", pages_or_sheets=1, created_at=datetime.now(UTC), status="ready")

    class Reg:
        def get_by_sha256(self, *a):
            return None

        def list_by_owner(self, *a):
            return []

    async def one(i, gate):
        f = UploadFile(io.BytesIO(b"text %d" % i), filename="a.txt")
        return await uploads.process_upload(f, "u", Slow(), Reg(), gate=gate)

    async def run(gate):
        return await asyncio.gather(*(one(i, gate) for i in range(6)))

    assert all(r.ok for r, _ in asyncio.run(run(asyncio.Semaphore(2))))
    assert peak == 2


def test_global_mint_limit_beats_ip_rotation(monkeypatch):
    app = make(monkeypatch, ANON_MINT_GLOBAL_PER_HOUR=3, ANON_MINT_RATE_PER_HOUR=100,
               TRUSTED_PROXY="X-Forwarded-For")
    with TestClient(app) as c:
        codes = [c.post("/v1/auth/anonymous", headers={"X-Forwarded-For": f"10.0.0.{i}"}) for i in range(5)]
        assert [r.status_code for r in codes] == [200, 200, 200, 429, 429]
        assert codes[3].json()["code"] == "global_rate_limited" and "Retry-After" in codes[3].headers


def test_unknown_exception_class_never_reaches_client(monkeypatch, caplog):
    class SecretInternalBoom(Exception):
        pass

    def boom(path, mime):
        raise SecretInternalBoom("x")

    monkeypatch.setattr("app.services.ingestion.parse_uploaded_file", boom)
    with TestClient(make(monkeypatch)) as c, caplog.at_level(logging.INFO):
        r = up(c, hdr("alice"))
        res = r.json()["results"][0]
        assert res["error"]["code"] == "ingest_failed" and res["failure_reason"] == "ingest_failed"
        assert "SecretInternalBoom" not in r.text
        listing = c.get("/v1/documents", headers=hdr("alice"))
        assert "SecretInternalBoom" not in listing.text
        assert listing.json()["items"][0]["failure_reason"] == "ingest_failed"
    assert "SecretInternalBoom" in caplog.text  # full class is still logged


def test_security_headers_on_v1_only(monkeypatch):
    with TestClient(make(monkeypatch)) as c:
        for r in (c.get("/v1/documents", headers=bearer("a")), c.get("/v1/documents"),
                  c.post("/v1/auth/anonymous")):
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            assert r.headers["Cache-Control"] == "no-store"
        assert "X-Content-Type-Options" not in c.get("/health").headers


def test_stream_keeps_cache_control_no_cache(monkeypatch):
    with TestClient(make(monkeypatch)) as c:
        h = bearer("alice")
        doc = up(c, h, body=b"The sky is blue. Grass is green.").json()["results"][0]["doc_id"]
        r = c.post("/v1/query", json={"question": "sky?", "doc_ids": [doc], "stream": True}, headers=h)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["Cache-Control"] == "no-cache"
        assert r.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("url", ["/v1/compare", "/v1/documents/x/summary", "/v1/documents/x/sentiment"])
def test_json_body_limit_on_compare_and_docintel(monkeypatch, url):
    with TestClient(make(monkeypatch)) as c:
        r = c.post(url, content=b"{" + b" " * 70_000 + b"}",
                   headers={**bearer("a"), "Content-Type": "application/json"})
        assert r.status_code == 413 and r.json()["code"] == "too_large"


def test_ping_checkpoint_removed(monkeypatch):
    with TestClient(make(monkeypatch)) as c:
        assert c.get("/ping-checkpoint").status_code == 404

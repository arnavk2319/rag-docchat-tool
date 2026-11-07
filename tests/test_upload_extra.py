"""T-003 upload-security coverage, ported to POST /v1/documents (fakes only, no network)."""

import asyncio
import tempfile
from pathlib import Path

import pytest
from app.api import uploads
from app.core import security as sec
from upload_harness import StubIngestion, first, make_client, post, zip_bytes


@pytest.fixture
def tmpdir_used(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


@pytest.fixture
def stub():
    return StubIngestion()


@pytest.fixture
def client(tmpdir_used, stub):
    return make_client(stub)


def leftovers(d: Path) -> list[Path]:
    return list(d.iterdir())


def test_ping():
    from app.main import app
    from fastapi.testclient import TestClient

    assert TestClient(app).get("/ping-checkpoint").status_code == 200


def test_temp_removed_on_success(client, tmpdir_used, stub):
    assert first(post(client, "a.txt", b"hi"))["ok"] is True
    assert len(stub.calls) == 1 and Path(stub.calls[0][0]).parent == tmpdir_used
    assert leftovers(tmpdir_used) == []


def test_temp_removed_when_ingest_raises(tmpdir_used):
    def boom(p, m):
        raise RuntimeError("kaboom-internal")

    c = make_client(StubIngestion(boom))
    resp = post(c, "a.txt", b"hi")
    assert resp.status_code == 200
    r = first(resp)
    assert r["ok"] is False and r["error"]["code"] == "ingest_failed"
    assert "kaboom" not in resp.text and "RuntimeError" not in resp.text
    assert leftovers(tmpdir_used) == []


def test_temp_removed_if_already_deleted(tmpdir_used):
    c = make_client(StubIngestion(lambda p, m: Path(p).unlink()))
    assert first(post(c, "a.txt", b"hi"))["ok"] is True


@pytest.mark.parametrize("name,data,code", [
    ("a.exe", b"MZ", "bad_extension"),
    ("noext", b"hi", "bad_extension"),
    ("a.txt", b"", "empty_file"),
    ("a.txt", b"\x00\x01\xff", "bad_content_type"),
    ("a.pdf", b"just text", "content_mismatch"),
    ("a.pdf", zip_bytes("word/document.xml"), "content_mismatch"),
    ("a.docx", b"plain text", "content_mismatch"),
    ("a.docx", zip_bytes("readme.txt"), "bad_content_type"),
    ("a.xlsx", zip_bytes("readme.txt"), "bad_content_type"),
    ("a.docx", zip_bytes("xl/workbook.xml"), "content_mismatch"),
    ("a.xlsx", zip_bytes("word/document.xml"), "content_mismatch"),
    ("a.txt", b"%PDF-1.4", "content_mismatch"),
])
def test_rejections_create_no_temp_and_skip_ingest(client, tmpdir_used, stub, name, data, code):
    resp = post(client, name, data)
    assert resp.status_code == (200 if code == "empty_file" else 415)
    r = first(resp)
    assert r["ok"] is False and r["error"]["code"] == code
    assert stub.calls == []
    assert leftovers(tmpdir_used) == []


def test_missing_filename(client, stub):
    resp = client.post("/v1/documents", files={"files": ("", b"hi")}, headers={"X-User-Id": "u1"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert isinstance(body["detail"], str) and body["detail"] and "hi" not in body["detail"].split()
    assert stub.calls == []


def test_txt_csv_reach_ingest_with_mime(client, stub):
    for name, data in [("a.txt", b"hello"), ("a.csv", b"a,b\n1,2\n"), ("b.csv", "naïve,ü\n".encode())]:
        assert first(post(client, name, data))["ok"] is True
    assert [c[1] for c in stub.calls] == ["text/plain", "text/csv", "text/csv"]


def test_xlsx_and_docx_and_pdf_accepted_xls_rejected(client, stub):
    assert first(post(client, "a.xlsx", zip_bytes("xl/workbook.xml")))["ok"]
    assert first(post(client, "a.docx", zip_bytes("word/document.xml")))["ok"]
    assert first(post(client, "a.pdf", b"%PDF-1.7\n..."))["ok"]
    assert len(stub.calls) == 3
    r = post(client, "a.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 20)
    assert r.status_code == 415 and first(r)["error"]["code"] == "bad_extension"


def test_oversize_rejected_over_http(client, stub, tmpdir_used):
    r = first(post(client, "a.txt", b"a" * (sec.MAX_FILE_SIZE + 1)))
    assert r["ok"] is False and r["error"]["code"] == "too_large"
    assert stub.calls == [] and leftovers(tmpdir_used) == []


def test_exactly_at_limit_ok(client):
    assert first(post(client, "a.txt", b"a" * sec.MAX_FILE_SIZE))["ok"] is True


def test_read_limited_stops_early_on_endless_stream():
    class Endless:
        reads = 0

        async def read(self, n):
            Endless.reads += 1
            return b"a" * n

    with pytest.raises(sec.UploadRejected) as ei:
        asyncio.run(sec.read_limited(Endless(), limit=1000))
    assert ei.value.code == "too_large"
    assert Endless.reads * sec.CHUNK_SIZE <= 1000 + sec.CHUNK_SIZE  # stopped at first chunk over limit


@pytest.mark.parametrize("name", ["../../x.txt", "..\\..\\x.txt", "/etc/passwd.txt", "résumé 文档 ☃.txt", "a\x00b.txt"])
def test_filename_does_not_influence_temp_path(client, tmpdir_used, stub, monkeypatch, name):
    real = tempfile.NamedTemporaryFile
    captured: list[dict] = []

    def spy(*args, **kwargs):
        captured.append({"args": args, **kwargs})
        return real(*args, **kwargs)

    monkeypatch.setattr(uploads.tempfile, "NamedTemporaryFile", spy)
    resp = post(client, name, b"hello")
    r = first(resp) if resp.status_code == 200 else None
    if r is None or not r["ok"]:  # odd names may legitimately be rejected
        assert captured == [] and leftovers(tmpdir_used) == []
        return
    assert len(captured) == 1
    kw = captured[0]
    assert kw["args"] == () and kw.get("suffix") == ".txt"
    assert kw.get("dir") is None and kw.get("prefix") is None and kw.get("mode") is None
    p = Path(stub.calls[0][0])
    assert p.parent == tmpdir_used
    assert p.name.startswith("tmp") and p.name.endswith(".txt")
    assert ".." not in str(p) and "\\" not in p.name and "\x00" not in str(p)
    assert leftovers(tmpdir_used) == []
    assert not (tmpdir_used.parent / "x.txt").exists()
    # the stored filename is a basename only
    assert "/" not in stub.calls[0][2] and "\\" not in stub.calls[0][2]

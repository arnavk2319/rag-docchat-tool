"""T-003 acceptance coverage for the upload router (fakes only, no network/models)."""

import asyncio
import io
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest
from app.routers import response as r
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def tmpdir_used(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


@pytest.fixture
def seen(monkeypatch):
    calls: list[str] = []

    def parse(path, mime):
        calls.append(path)
        return "ok"

    monkeypatch.setattr(r, "parse_document", parse)
    return calls


@pytest.fixture
def client(tmpdir_used, seen):
    app = FastAPI()
    app.include_router(r.router)
    return TestClient(app, raise_server_exceptions=False)


def post(client, name, data):
    return client.post("/validate-upload", files={"file": (name, data)})


def zip_bytes(member: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(member, "x")
    return b.getvalue()


def leftovers(d: Path) -> list[Path]:
    return list(d.iterdir())


def test_ping(client):
    assert client.get("/ping-checkpoint").status_code == 200


def test_temp_removed_on_success(client, tmpdir_used, seen):
    assert post(client, "a.txt", b"hi").status_code == 200
    assert len(seen) == 1 and Path(seen[0]).parent == tmpdir_used
    assert leftovers(tmpdir_used) == []


def test_temp_removed_when_parser_returns_none(client, tmpdir_used, monkeypatch):
    monkeypatch.setattr(r, "parse_document", lambda p, m: None)
    assert post(client, "a.txt", b"hi").status_code == 422
    assert leftovers(tmpdir_used) == []


def test_temp_removed_when_parser_raises(client, tmpdir_used, monkeypatch):
    def boom(p, m):
        raise RuntimeError("kaboom-internal")

    monkeypatch.setattr(r, "parse_document", boom)
    resp = post(client, "a.txt", b"hi")
    assert resp.status_code == 500
    assert "kaboom" not in resp.text and "RuntimeError" not in resp.text
    assert leftovers(tmpdir_used) == []


def test_temp_removed_when_parser_raises_http_error(client, tmpdir_used, monkeypatch):
    def nope(p, m):
        raise HTTPException(status_code=418, detail="teapot")

    monkeypatch.setattr(r, "parse_document", nope)
    assert post(client, "a.txt", b"hi").status_code == 418
    assert leftovers(tmpdir_used) == []


def test_temp_removed_if_already_deleted(client, tmpdir_used, monkeypatch):
    def rm_then_ok(p, m):
        Path(p).unlink()
        return "ok"

    monkeypatch.setattr(r, "parse_document", rm_then_ok)
    assert post(client, "a.txt", b"hi").status_code == 200


@pytest.mark.parametrize("name,data,code", [
    ("a.exe", b"MZ", 400),
    ("noext", b"hi", 400),
    ("a.txt", b"", 400),
    ("a.txt", b"\x00\x01\xff", 400),
    ("a.pdf", b"just text", 400),
    ("a.pdf", zip_bytes("word/document.xml"), 400),
    ("a.docx", b"plain text", 400),
    ("a.docx", zip_bytes("readme.txt"), 400),
    ("a.xlsx", zip_bytes("readme.txt"), 400),
    ("a.docx", zip_bytes("xl/workbook.xml"), 400),
    ("a.xlsx", zip_bytes("word/document.xml"), 400),
    ("a.txt", b"%PDF-1.4", 400),
])
def test_rejections_create_no_temp_and_skip_parser(client, tmpdir_used, seen, name, data, code):
    assert post(client, name, data).status_code == code
    assert seen == []
    assert leftovers(tmpdir_used) == []


def test_missing_filename(client, seen):
    resp = client.post("/validate-upload", files={"file": ("", b"hi")})
    assert resp.status_code in (400, 422)
    assert seen == []


def test_txt_csv_reach_parser_with_mime(client, monkeypatch):
    got = []
    monkeypatch.setattr(r, "parse_document", lambda p, m: got.append(m) or "ok")
    assert post(client, "a.txt", b"hello").status_code == 200
    assert post(client, "a.csv", b"a,b\n1,2\n").status_code == 200
    assert post(client, "b.csv", "naïve,ü\n".encode()).status_code == 200
    assert got == ["text/plain", "text/csv", "text/csv"]


def test_xlsx_and_docx_and_pdf_and_xls_accepted(client, seen):
    assert post(client, "a.xlsx", zip_bytes("xl/workbook.xml")).status_code == 200
    assert post(client, "a.docx", zip_bytes("word/document.xml")).status_code == 200
    assert post(client, "a.pdf", b"%PDF-1.7\n...").status_code == 200
    assert post(client, "a.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 20).status_code == 200
    assert len(seen) == 4


def test_oversize_413_over_http(client, seen, tmpdir_used):
    assert post(client, "a.txt", b"a" * (r.MAX_FILE_SIZE + 1)).status_code == 413
    assert seen == [] and leftovers(tmpdir_used) == []


def test_exactly_at_limit_ok(client):
    assert post(client, "a.txt", b"a" * r.MAX_FILE_SIZE).status_code == 200


def test_read_limited_stops_early_on_endless_stream():
    class Endless:
        reads = 0

        async def read(self, n):
            Endless.reads += 1
            return b"a" * n

    with pytest.raises(HTTPException) as ei:
        asyncio.run(r.read_limited(Endless(), limit=1000))  # type: ignore[arg-type]
    assert ei.value.status_code == 413
    assert Endless.reads * r.CHUNK_SIZE <= 1000 + r.CHUNK_SIZE  # stopped at first chunk over limit


@pytest.mark.parametrize("name", ["../../x.txt", "..\\..\\x.txt", "/etc/passwd.txt", "résumé 文档 ☃.txt", "a\x00b.txt"])
def test_filename_does_not_influence_temp_path(client, tmpdir_used, seen, monkeypatch, name):
    real = tempfile.NamedTemporaryFile
    captured: list[dict] = []

    def spy(*args, **kwargs):
        captured.append({"args": args, **kwargs})
        return real(*args, **kwargs)

    monkeypatch.setattr(r.tempfile, "NamedTemporaryFile", spy)
    resp = post(client, name, b"hello")
    if resp.status_code != 200:  # servers may legitimately reject odd names
        assert resp.status_code in (400, 422)
        assert captured == [] and leftovers(tmpdir_used) == []
        return
    # code passes only the sanitized suffix; dir/prefix/name stay tempfile defaults
    assert len(captured) == 1
    kw = captured[0]
    assert kw["args"] == () and kw.get("suffix") == ".txt"
    assert kw.get("dir") is None and kw.get("prefix") is None and kw.get("mode") is None
    p = Path(seen[0])
    assert p.parent == tmpdir_used  # not any directory from the client name
    assert p.name.startswith("tmp") and p.name.endswith(".txt")
    assert ".." not in str(p) and "\\" not in p.name and "\x00" not in str(p)
    assert leftovers(tmpdir_used) == []
    assert not (tmpdir_used.parent / "x.txt").exists()


def test_lazy_import_returns_501_until_t004(tmpdir_used, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.utilities.file_handler", None)  # forces ImportError
    app = FastAPI()
    app.include_router(r.router)
    c = TestClient(app, raise_server_exceptions=False)
    resp = post(c, "a.txt", b"hi")
    assert resp.status_code == 501
    assert leftovers(tmpdir_used) == []


def test_summary_is_none_until_llm_wrapper(client):
    assert post(client, "a.txt", b"hi").json()["summary"] is None

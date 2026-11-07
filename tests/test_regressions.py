import io
import subprocess
import sys
import zipfile

import pytest
from app.routers import response as r
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(r, "parse_document", lambda path, mime: f"parsed:{mime}")
    app = FastAPI()
    app.include_router(r.router)
    return TestClient(app)


def post(client, name, data):
    return client.post("/validate-upload", files={"file": (name, data)})


def zip_bytes(member: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(member, "x")
    return b.getvalue()


def test_rejected_upload_does_not_raise_unbound_local(client):
    # bug: finally os.remove(tmp_path) -> UnboundLocalError when no temp file made
    resp = post(client, "a.exe", b"MZ")
    assert resp.status_code == 400


def test_oversize_rejected_while_streaming(client, monkeypatch):
    monkeypatch.setattr(r, "MAX_FILE_SIZE", 100)
    monkeypatch.setattr(r, "CHUNK_SIZE", 10)
    resp = post(client, "a.txt", b"a" * 500)
    assert resp.status_code == 413


@pytest.mark.parametrize("name,data,mime", [
    ("a.txt", b"hello", "text/plain"),
    ("a.csv", b"a,b\n1,2\n", "text/csv"),
])
def test_text_and_csv_are_sniffed_not_skipped(client, name, data, mime):
    resp = post(client, name, data)
    assert resp.status_code == 200
    assert resp.json()["content"] == f"parsed:{mime}"


def test_xlsx_allowed(client):
    resp = post(client, "a.xlsx", zip_bytes("xl/workbook.xml"))
    assert resp.status_code == 200
    assert resp.json()["content"].endswith("spreadsheetml.sheet")


def test_extension_must_match_content(client):
    assert post(client, "a.pdf", zip_bytes("word/document.xml")).status_code == 400
    assert post(client, "a.txt", b"%PDF-1.4 x").status_code == 400
    assert post(client, "a.docx", b"plain text").status_code == 400


def test_binary_garbage_rejected(client):
    assert post(client, "a.txt", b"\x00\x01\x02\xff").status_code == 400


def test_parse_none_is_an_error_not_success(client, monkeypatch):
    monkeypatch.setattr(r, "parse_document", lambda p, m: None)
    assert post(client, "a.txt", b"hi").status_code == 422


def test_temp_file_removed(client, monkeypatch):
    seen = []
    monkeypatch.setattr(r, "parse_document", lambda p, m: seen.append(p) or "ok")
    assert post(client, "a.txt", b"hi").status_code == 200
    import os
    assert not os.path.exists(seen[0])


def test_error_detail_does_not_leak_internals(client, monkeypatch):
    def boom(p, m):
        raise RuntimeError("secret-path /etc/x")
    monkeypatch.setattr(r, "parse_document", boom)
    resp = post(client, "a.txt", b"hi")
    assert resp.status_code == 500
    assert "secret-path" not in resp.text


def test_http_exception_log_has_no_dollar_placeholders(caplog):
    import asyncio

    from app.main import http_exception_handle_logging
    from fastapi import HTTPException
    with caplog.at_level("ERROR", logger="uvicorn"):
        asyncio.run(http_exception_handle_logging(None, HTTPException(status_code=400, detail="bad")))
    assert "$" not in caplog.text
    assert "400 bad" in caplog.text


def test_import_main_opens_no_network():
    code = (
        "import socket\n"
        "def deny(*a, **k): raise RuntimeError('network used')\n"
        "socket.socket.connect = deny\n"
        "socket.create_connection = deny\n"
        "import app.main\n"
    )
    out = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def _big_zip(member: str, size: int = 20000) -> bytes:
    import os as _os
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_STORED) as z:
        z.writestr(member, _os.urandom(size))
    return b.getvalue()


@pytest.mark.parametrize("name", ["a.docx", "a.xlsx"])
def test_large_plain_zip_rejected(client, name):
    assert post(client, name, _big_zip("notes/readme.txt")).status_code == 400


@pytest.mark.parametrize("name,member", [("a.docx", "word/document.xml"), ("a.xlsx", "xl/workbook.xml")])
def test_large_valid_ooxml_accepted(client, name, member):
    data = _big_zip(member)
    assert len(data) > 8192
    assert post(client, name, data).status_code == 200


def test_corrupt_zip_rejected(client):
    data = _big_zip("word/document.xml")[:15000]
    assert post(client, "a.docx", data).status_code == 400


def test_zip_declaring_huge_sizes_rejected(client, monkeypatch):
    monkeypatch.setattr(r, "MAX_ZIP_UNCOMPRESSED", 10)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("word/document.xml", "x" * 100)
    assert post(client, "a.docx", b.getvalue()).status_code == 400


def test_zip_with_too_many_members_rejected(client, monkeypatch):
    monkeypatch.setattr(r, "MAX_ZIP_MEMBERS", 2)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for i in range(3):
            z.writestr(f"word/{i}.xml", "x")
    assert post(client, "a.docx", b.getvalue()).status_code == 400

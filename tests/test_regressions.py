import io
import subprocess
import sys
import zipfile

import pytest
from app.core import security as sec
from upload_harness import StubIngestion, first, make_client, post, zip_bytes


@pytest.fixture
def stub():
    return StubIngestion()


@pytest.fixture
def client(stub):
    return make_client(stub)


def code(resp):
    r = first(resp)
    return None if r["ok"] else r["error"]["code"]


def test_rejected_upload_does_not_raise_unbound_local(client):
    # bug: finally os.remove(tmp_path) -> UnboundLocalError when no temp file made
    resp = post(client, "a.exe", b"MZ")
    assert resp.status_code == 415 and code(resp) == "bad_extension"


def test_oversize_rejected_while_streaming(client, monkeypatch):
    monkeypatch.setattr(sec, "MAX_FILE_SIZE", 100)
    monkeypatch.setattr(sec, "CHUNK_SIZE", 10)
    assert code(post(client, "a.txt", b"a" * 500)) == "too_large"


@pytest.mark.parametrize("name,data,mime", [
    ("a.txt", b"hello", "text/plain"),
    ("a.csv", b"a,b\n1,2\n", "text/csv"),
])
def test_text_and_csv_are_sniffed_not_skipped(client, stub, name, data, mime):
    assert first(post(client, name, data))["ok"] is True
    assert stub.calls[-1][1] == mime


def test_xlsx_allowed(client, stub):
    assert first(post(client, "a.xlsx", zip_bytes("xl/workbook.xml")))["ok"]
    assert stub.calls[-1][1].endswith("spreadsheetml.sheet")


def test_extension_must_match_content(client):
    assert code(post(client, "a.pdf", zip_bytes("word/document.xml"))) == "content_mismatch"
    assert code(post(client, "a.txt", b"%PDF-1.4 x")) == "content_mismatch"
    assert code(post(client, "a.docx", b"plain text")) == "content_mismatch"


def test_binary_garbage_rejected(client):
    assert code(post(client, "a.txt", b"\x00\x01\x02\xff")) == "bad_content_type"


def test_temp_file_removed(client, stub):
    import os

    assert first(post(client, "a.txt", b"hi"))["ok"]
    assert not os.path.exists(stub.calls[0][0])


def test_error_detail_does_not_leak_internals():
    def boom(p, m):
        raise RuntimeError("secret-path /etc/x")

    resp = post(make_client(StubIngestion(boom)), "a.txt", b"hi")
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
    assert code(post(client, name, _big_zip("notes/readme.txt"))) == "bad_content_type"


@pytest.mark.parametrize("name,member", [("a.docx", "word/document.xml"), ("a.xlsx", "xl/workbook.xml")])
def test_large_valid_ooxml_accepted(client, name, member):
    data = _big_zip(member)
    assert len(data) > 8192
    assert first(post(client, name, data))["ok"]


def test_corrupt_zip_rejected(client):
    data = _big_zip("word/document.xml")[:15000]
    assert code(post(client, "a.docx", data)) == "bad_content_type"


def test_zip_declaring_huge_sizes_rejected(client, monkeypatch):
    monkeypatch.setattr(sec, "MAX_ZIP_UNCOMPRESSED", 10)
    assert code(post(client, "a.docx", zip_bytes("word/document.xml", "x" * 100))) == "bad_content_type"


def test_zip_with_too_many_members_rejected(client, monkeypatch):
    monkeypatch.setattr(sec, "MAX_ZIP_MEMBERS", 2)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for i in range(3):
            z.writestr(f"word/{i}.xml", "x")
    assert code(post(client, "a.docx", b.getvalue())) == "bad_content_type"

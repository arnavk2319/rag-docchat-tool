"""T-008: upload hardening at the HTTP layer (413/415, streaming cap, filename, temp files)."""

import asyncio
import tempfile
from pathlib import Path

import pytest
from app.core import security as sec
from upload_harness import StubIngestion, make_client, post


@pytest.fixture
def tmpd(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def left(d: Path) -> list[Path]:
    return list(d.iterdir())


def test_oversize_is_413_and_file_not_fully_read(tmpd):
    stub = StubIngestion()
    c = make_client(stub)
    r = post(c, "a.txt", b"a" * (sec.MAX_FILE_SIZE + 1))
    assert r.status_code == 413
    assert r.json()["results"][0]["error"]["code"] == "too_large"
    assert stub.calls == [] and left(tmpd) == []


def test_reader_stops_before_consuming_whole_stream():
    consumed = 0

    class Src:
        async def read(self, n):
            nonlocal consumed
            consumed += n
            return b"a" * n

    total = 100 * 1024 * 1024
    with pytest.raises(sec.UploadRejected):
        asyncio.run(sec.read_limited(Src()))
    assert consumed < total
    assert consumed <= sec.MAX_FILE_SIZE + sec.CHUNK_SIZE


@pytest.mark.parametrize(
    "name,data",
    [
        ("a.exe", b"MZ\x90\x00"),
        ("a.pdf", b"MZ\x90\x00" + b"\x00" * 50),
        ("a.docx", b"%PDF-1.7\n"),
    ],
)
def test_bad_extension_or_sniff_mismatch_is_415(tmpd, name, data):
    stub = StubIngestion()
    r = post(make_client(stub), name, data)
    assert r.status_code == 415
    assert stub.calls == [] and left(tmpd) == []


def test_traversal_filename_sanitized(tmpd):
    stub = StubIngestion()
    r = post(make_client(stub), "../../etc/passwd.pdf", b"%PDF-1.7\n...")
    assert r.status_code == 200
    assert r.json()["results"][0]["filename"] == "passwd.pdf"
    assert stub.calls[0][2] == "passwd.pdf"
    assert Path(stub.calls[0][0]).parent == tmpd


def test_temp_removed_on_success_and_parser_exception(tmpd):
    ok = StubIngestion()
    assert post(make_client(ok), "a.txt", b"hello").status_code == 200
    assert len(ok.calls) == 1 and left(tmpd) == []

    def boom(path, mime):
        assert Path(path).exists()
        raise RuntimeError("parser exploded")

    bad = StubIngestion(behaviour=boom)
    r = post(make_client(bad), "a.txt", b"hello")
    assert r.json()["results"][0]["error"]["code"] == "ingest_failed"
    assert left(tmpd) == []


def test_max_upload_mb_lowers_cap(tmpd, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    try:
        stub = StubIngestion()
        r = post(make_client(stub), "a.txt", b"a" * (1024 * 1024 + 1))
        assert r.status_code == 413 and stub.calls == [] and left(tmpd) == []
    finally:
        monkeypatch.delenv("MAX_UPLOAD_MB")
        get_settings.cache_clear()

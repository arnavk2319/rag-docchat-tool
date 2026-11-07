"""T-004 extra coverage: registry lifecycle, run_blocking, parsers, upload e2e, legacy removal, config."""

import asyncio
import io
import re
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import pytest
from app.core import models
from app.core.concurrency import run_blocking
from app.core.config import Settings, get_settings
from app.utilities import file_handler as fh
from authutil import bearer
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "backend"


# ---------- registry ----------


def _counting(monkeypatch):
    import app.main as m

    calls = {"e": 0, "r": 0}

    def fake_load(settings):
        def e(_):
            calls["e"] += 1
            return object()

        def rr(_):
            calls["r"] += 1
            return object()

        return models.load_models(settings, embedder_loader=e, reranker_loader=rr)

    monkeypatch.setattr(m, "load_models", fake_load)
    return m, calls


def test_two_lifespans_each_load_once(monkeypatch):
    m, calls = _counting(monkeypatch)
    with TestClient(m.app) as c:
        c.get("/healthz")
    assert calls == {"e": 1, "r": 1}
    models.reset_models()
    with TestClient(m.app) as c:
        c.get("/healthz")
        c.get("/healthz")
    assert calls == {"e": 2, "r": 2}


def test_second_lifespan_reloads_registry_after_shutdown_reset(monkeypatch):
    # changed in review round 1: shutdown resets the registry, so each lifespan loads once
    m, calls = _counting(monkeypatch)
    with TestClient(m.app):
        pass
    with TestClient(m.app):
        pass
    assert calls == {"e": 2, "r": 2}


def test_load_models_concurrent_calls_load_once():
    calls = []

    def slow(_):
        calls.append(1)
        time.sleep(0.05)
        return object()

    out = []
    ts = [
        threading.Thread(
            target=lambda: out.append(
                models.load_models(Settings(), embedder_loader=slow, reranker_loader=slow)
            )
        )
        for _ in range(8)
    ]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 2 and len({id(o) for o in out}) == 1


def test_loader_receives_config_ids(monkeypatch):
    monkeypatch.setenv("EMBED_MODEL", "x/embed")
    monkeypatch.setenv("RERANK_MODEL", "x/rerank")
    get_settings.cache_clear()
    seen = []
    models.load_models(
        get_settings(),
        embedder_loader=lambda i: seen.append(i) or 1,
        reranker_loader=lambda i: seen.append(i) or 2,
    )
    assert seen == ["x/embed", "x/rerank"]


def test_not_loaded_before_startup_and_get_models_raises():
    assert models.models_loaded() is False
    with pytest.raises(RuntimeError):
        models.get_models()
    import app.main as m

    assert TestClient(m.app).get("/healthz").json()["models_loaded"] is False


def test_lifespan_shutdown_clean_and_loader_failure_propagates(monkeypatch):
    import app.main as m

    with TestClient(m.app) as c:
        assert c.get("/healthz").status_code == 200
    # exiting the context must not raise; threads from the pool are not leaked as non-daemon blockers

    def boom(_):
        raise OSError("no weights")

    monkeypatch.setattr(m, "load_models", lambda s: models.load_models(s, embedder_loader=boom))
    models.reset_models()
    with pytest.raises(OSError), TestClient(m.app):
        pass
    assert models.models_loaded() is False


def test_import_main_does_not_load_models_or_import_st():
    code = (
        "import os, sys\n"
        "os.environ.pop('MODELS_FAKE', None)\n"
        "os.environ['LLM_PROVIDER']='mock'\n"
        "import app.core.models as mo\n"
        "def boom(*a, **k): raise AssertionError('loader called at import')\n"
        "mo._real_embedder = boom; mo._real_reranker = boom\n"
        "import app.main\n"
        "assert mo.models_loaded() is False\n"
        "assert 'sentence_transformers' not in sys.modules\n"
        "assert 'torch' not in sys.modules\n"
    )
    res = subprocess.run(
        check=False,
        args=[sys.executable, "-I", "-c", code],
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin", "HOME": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr[-800:]


# ---------- run_blocking ----------


async def test_run_blocking_args_kwargs_style_and_exceptions():
    assert await run_blocking(lambda a, b: a + b, 2, 3) == 5

    class MyErr(ValueError):
        pass

    def bad():
        raise MyErr("x")

    with pytest.raises(MyErr):
        await run_blocking(bad)


async def test_run_blocking_runs_off_loop_thread():
    main = threading.get_ident()
    assert await run_blocking(threading.get_ident) != main


async def test_run_blocking_cancellation_does_not_hang():
    t = asyncio.create_task(run_blocking(time.sleep, 0.3))
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(t, 1)


async def test_run_blocking_many_concurrent_no_deadlock():
    res = await asyncio.wait_for(
        asyncio.gather(*[run_blocking(lambda i: (time.sleep(0.01), i)[1], i) for i in range(200)]),
        15,
    )
    assert res == list(range(200))


# ---------- parsers ----------


def _pdf(path: Path, pages: int = 2, text: bool = True):
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    w = PdfWriter()
    for i in range(1, pages + 1):
        p = w.add_blank_page(width=200, height=200)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            p[NameObject("/Resources")] = DictionaryObject(
                {
                    NameObject("/Font"): DictionaryObject(
                        {NameObject("/F1"): w._add_object(font)}
                    )
                }
            )
            s = DecodedStreamObject()
            s.set_data(f"BT /F1 12 Tf 20 100 Td (hello page{i}) Tj ET".encode())
            p[NameObject("/Contents")] = w._add_object(s)
    with open(path, "wb") as f:
        w.write(f)


def test_pdf_pages_one_based(tmp_path):
    p = tmp_path / "a.pdf"
    _pdf(p, 3)
    out = fh.load_pdf(str(p))
    assert [x.page for x in out] == [1, 2, 3]
    assert [x.source for x in out] == ["page 1", "page 2", "page 3"]
    assert "page2" in out[1].text and all(x.sheet is None for x in out)


def test_pdf_blank_pages_give_empty_text(tmp_path):
    p = tmp_path / "b.pdf"
    _pdf(p, 1, text=False)
    out = fh.load_pdf(str(p))
    assert len(out) == 1 and out[0].text == "" and out[0].page == 1


def test_docx_tables_and_source(tmp_path):
    import docx

    d = docx.Document()
    d.add_paragraph("intro")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text = "a"
    t.rows[0].cells[1].text = "b"
    p = tmp_path / "t.docx"
    d.save(str(p))
    out = fh.load_docx(str(p))
    assert len(out) == 1 and "intro" in out[0].text and "a | b" in out[0].text
    assert out[0].page is None and out[0].sheet is None


def test_xlsx_multiple_sheets_tagged(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "Sales"
    wb.active.append(["k", 5])
    ws = wb.create_sheet("Costs")
    ws.append(["z", None, 3])
    p = tmp_path / "m.xlsx"
    wb.save(str(p))
    out = fh.load_xlsx(str(p))
    assert [x.sheet for x in out] == ["Sales", "Costs"]
    assert [x.source for x in out] == ["sheet Sales", "sheet Costs"]
    assert "k, 5" in out[0].text and "z" in out[1].text and "3" in out[1].text


def test_csv_utf8_unicode(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text("n,v\ncafé,1\n", encoding="utf-8")
    assert "café, 1" in fh.load_csv(str(p))[0].text


def test_txt_utf8_unicode(tmp_path):
    p = tmp_path / "u.txt"
    p.write_text("naïve café", encoding="utf-8")
    assert fh.load_txt(str(p))[0].text == "naïve café"


def test_txt_bom_is_stripped_or_documented(tmp_path):
    p = tmp_path / "bom.txt"
    p.write_bytes(b"\xef\xbb\xbfhello")
    out = fh.load_txt(str(p))[0].text
    assert out.lstrip("﻿") == "hello"
    assert not out.startswith("﻿"), "BOM leaks into extracted text"


def test_csv_bom_not_in_first_header(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(b"\xef\xbb\xbfname,v\nx,1\n")
    assert not fh.load_csv(str(p))[0].text.startswith("﻿"), "BOM leaks into first CSV cell"


@pytest.mark.parametrize("fn", [fh.load_txt, fh.load_csv])
def test_latin1_text_raises_parse_error_not_garbage(tmp_path, fn):
    p = tmp_path / "l1"
    p.write_bytes("café".encode("latin-1"))
    with pytest.raises(fh.ParseError):
        fn(str(p))


def test_empty_files(tmp_path):
    p = tmp_path / "e.txt"
    p.write_bytes(b"")
    assert fh.load_txt(str(p))[0].text == ""
    c = tmp_path / "e.csv"
    c.write_bytes(b"")
    assert fh.load_csv(str(c))[0].text == ""
    for fn, n in [(fh.load_pdf, "e.pdf"), (fh.load_docx, "e.docx"), (fh.load_xlsx, "e.xlsx")]:
        q = tmp_path / n
        q.write_bytes(b"")
        with pytest.raises(fh.ParseError):
            fn(str(q))


def test_parse_error_chain_preserved_and_no_prints(tmp_path, capsys):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"not a pdf")
    with pytest.raises(fh.ParseError) as ei:
        fh.load_pdf(str(p))
    assert ei.value.__cause__ is not None
    fh.load_txt(str(_w(tmp_path / "ok.txt", b"hi")))
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""


def _w(p: Path, b: bytes) -> Path:
    p.write_bytes(b)
    return p


def test_xls_clear_parse_error(tmp_path):
    p = _w(tmp_path / "a.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    with pytest.raises(fh.ParseError) as ei:
        fh.parse_uploaded_file(str(p), fh.XLS)
    assert "Excel" in str(ei.value)


def test_decompression_limit_zip_bomb_vs_legit_large(tmp_path, monkeypatch):
    import docx

    d = docx.Document()
    d.add_paragraph("x" * 50)
    legit = tmp_path / "legit.docx"
    d.save(str(legit))
    monkeypatch.setattr(fh, "MAX_DECOMPRESSED_BYTES", 5_000_000)
    # large (1 MB) but under cap: add a big media-like member
    big = tmp_path / "big.docx"
    with zipfile.ZipFile(legit) as zi, zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in zi.infolist():
            zo.writestr(i, zi.read(i))
        zo.writestr("word/media/big.bin", b"\0" * 1_000_000)
    assert "x" * 50 in fh.load_docx(str(big))[0].text
    # over the cap: forged declared size (tiny) is irrelevant, real bytes count
    bomb = tmp_path / "bomb.docx"
    with zipfile.ZipFile(legit) as zi, zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in zi.infolist():
            zo.writestr(i, zi.read(i))
        zo.writestr("word/media/huge.bin", b"\0" * 8_000_000)
    with pytest.raises(fh.DecompressionLimitError) as ei:
        fh.load_docx(str(bomb))
    assert isinstance(ei.value, fh.ParseError)


def test_forged_declared_size_still_rejected(tmp_path, monkeypatch):
    p = tmp_path / "forge.xlsx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", b"A" * 3_000_000)
    raw = bytearray(p.read_bytes())
    # patch declared uncompressed size (local + central headers) to 10
    sig_local, sig_cd = b"PK\x03\x04", b"PK\x01\x02"
    for sig, off in ((sig_local, 22), (sig_cd, 24)):
        i = raw.find(sig)
        raw[i + off : i + off + 4] = (10).to_bytes(4, "little")
    p.write_bytes(bytes(raw))
    monkeypatch.setattr(fh, "MAX_DECOMPRESSED_BYTES", 100_000)
    with pytest.raises(fh.ParseError):  # DecompressionLimitError or size-mismatch BadZipFile
        fh.load_xlsx(str(p))


def test_check_zip_bomb_rejects_non_zip(tmp_path):
    with pytest.raises(fh.ParseError):
        fh.check_zip_bomb(str(_w(tmp_path / "n.zip", b"nope")))


def test_file_handler_import_no_side_effects(tmp_path):
    code = (
        "import sys, io, os\n"
        "o = io.StringIO(); sys.stdout = o; sys.stderr = o\n"
        "before = set(os.listdir('.'))\n"
        "import app.utilities.file_handler\n"
        "assert o.getvalue() == '', o.getvalue()\n"
        "assert set(os.listdir('.')) == before\n"
        "for m in ('sentence_transformers','torch','langchain','pandas','pypdf','docx','openpyxl'):\n"
        "    assert m not in sys.modules, m\n"
    )
    res = subprocess.run(
        check=False,
        args=[sys.executable, "-I", "-c", code],
        cwd=tmp_path,
        env={"PYTHONPATH": str(SRC), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr[-800:]


# ---------- upload e2e (real app, fake models, tmp registry + Chroma) ----------


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    import app.main as m

    with TestClient(m.app, raise_server_exceptions=False) as c:
        yield c, tmp_path / "tmp"


def _up(c, name, data):
    r = c.post("/v1/documents", files={"files": (name, data)}, headers=bearer("u1"))
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


def _docx_bytes():
    import docx

    b = io.BytesIO()
    d = docx.Document()
    d.add_paragraph("hello docx")
    d.save(b)
    return b.getvalue()


def _xlsx_bytes():
    import openpyxl

    b = io.BytesIO()
    wb = openpyxl.Workbook()
    wb.active.title = "S1"
    wb.active.append([1, 2])
    wb.create_sheet("S2").append(["a"])
    wb.save(b)
    return b.getvalue()


def zip_with(member: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(member, "not xml SECRETTEXT")
    return b.getvalue()


def test_upload_each_type_ready(app_client, tmp_path):
    c, tmpd = app_client
    pdf = tmp_path / "x.pdf"
    _pdf(pdf, 2)
    for name, data in [
        ("a.txt", b"hello txt"),
        ("a.csv", b"a,b\n1,2\n"),
        ("a.docx", _docx_bytes()),
        ("a.xlsx", _xlsx_bytes()),
        ("a.pdf", pdf.read_bytes()),
    ]:
        r = _up(c, name, data)
        assert r["ok"] and r["status"] == "ready" and r["filename"] == name, (name, r)
    assert list(tmpd.iterdir()) == []


def test_upload_corrupt_files_fail_no_leak_and_cleanup(app_client):
    c, tmpd = app_client
    for name, data in [
        ("a.pdf", b"%PDF-1.4 SECRETTEXT junk"),
        ("a.docx", zip_with("word/document.xml")),
        ("a.xlsx", zip_with("xl/workbook.xml")),
    ]:
        r = _up(c, name, data)
        assert r["ok"] is False and r["error"]["code"] == "parse_error", (name, r)
        assert r["failure_reason"] == "parse_error"
        assert "SECRETTEXT" not in str(r) and "Traceback" not in str(r)
    assert list(tmpd.iterdir()) == []


def test_upload_decompression_bomb_fails(app_client, monkeypatch):
    c, tmpd = app_client
    monkeypatch.setattr(fh, "MAX_DECOMPRESSED_BYTES", 100_000)
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"\0" * 2_000_000)
    r = _up(c, "a.docx", b.getvalue())
    assert r["ok"] is False and r["error"]["code"] == "parse_error"
    assert list(tmpd.iterdir()) == []


def test_upload_real_import_error_is_ingest_failed_without_leak(app_client, monkeypatch):
    c, tmpd = app_client

    def boom(path):
        raise ImportError("SECRET-internal-module missing")

    monkeypatch.setitem(fh._LOADERS, fh.TXT, boom)
    r = _up(c, "a.txt", b"hi")
    assert r["ok"] is False and r["error"]["code"] == "ingest_failed"
    assert "SECRET" not in str(r) and "missing" not in str(r)
    assert list(tmpd.iterdir()) == []


# ---------- legacy removal ----------


def test_legacy_modules_gone_and_unreferenced():
    util = SRC / "app" / "utilities"
    assert not (util / "setup.py").exists()
    assert not (util / "file_security_handler.py").exists()
    pat = re.compile(
        r"file_security_handler|utilities\.setup|utilities import setup|from \.setup|from \.\.utilities\.setup|from \. import setup"
    )
    hits = []
    for f in list(SRC.rglob("*.py")) + list((ROOT / "tests").glob("*.py")):
        if f.name in {"test_t004_extra.py"}:
            continue
        if pat.search(f.read_text()):
            hits.append(str(f))
    assert hits == []


def test_legacy_router_and_langchain_gone():
    assert not (SRC / "app" / "routers").exists()
    for f in SRC.rglob("*.py"):
        assert "langchain" not in f.read_text() and "HttpClient" not in f.read_text(), f


# ---------- config ----------


def test_config_defaults_and_env_override(monkeypatch):
    monkeypatch.delenv("EMBED_MODEL", raising=False)
    monkeypatch.delenv("RERANK_MODEL", raising=False)
    get_settings.cache_clear()
    s = get_settings()
    assert s.embed_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert s.rerank_model == "BAAI/bge-reranker-base"
    monkeypatch.setenv("EMBED_MODEL", "a/b")
    monkeypatch.setenv("RERANK_MODEL", "c/d")
    get_settings.cache_clear()
    s = get_settings()
    assert (s.embed_model, s.rerank_model) == ("a/b", "c/d")

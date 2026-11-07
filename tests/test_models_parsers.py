"""T-004: model registry, run_blocking, healthz, parsers."""

import asyncio
import io
import subprocess
import sys
import time
import zipfile

import pytest
from app.core import models
from app.core.concurrency import run_blocking
from app.core.config import get_settings
from app.utilities import file_handler as fh
from fastapi.testclient import TestClient


def test_loader_called_once_across_requests(monkeypatch):
    import app.main as m

    calls = {"e": 0, "r": 0}

    def fake_load(settings):
        return models.load_models(
            settings,
            embedder_loader=lambda i: calls.__setitem__("e", calls["e"] + 1) or object(),
            reranker_loader=lambda i: calls.__setitem__("r", calls["r"] + 1) or object(),
        )

    monkeypatch.setattr(m, "load_models", fake_load)
    with TestClient(m.app) as c:
        for _ in range(3):
            assert c.get("/healthz").json()["models_loaded"] is True
    assert calls == {"e": 1, "r": 1}


def test_healthz_with_fakes():
    import app.main as m

    with TestClient(m.app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200 and r.json() == {"models_loaded": True, "fake": True}
        assert isinstance(models.get_models().embedder, models.FakeEmbedder)


def test_model_ids_from_config():
    s = get_settings()
    assert "MiniLM" in s.embed_model and "bge-reranker-base" in s.rerank_model


async def test_run_blocking_keeps_loop_ticking():
    ticks = 0

    async def beat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    t = asyncio.create_task(beat())
    assert await run_blocking(lambda x: (time.sleep(0.3), x)[1], 7) == 7
    t.cancel()
    assert ticks >= 5


def test_import_has_no_side_effects():
    code = "import sys, io; o=io.StringIO(); sys.stdout=o; import app.utilities.file_handler; assert o.getvalue()==''; assert 'sentence_transformers' not in sys.modules and 'langchain' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize("fn", [fh.load_pdf, fh.load_docx, fh.load_xlsx, fh.load_csv])
def test_corrupt_raises_parse_error(tmp_path, fn):
    p = tmp_path / "bad"
    p.write_bytes(b"\xff\x00garbage")
    with pytest.raises(fh.ParseError):
        fn(str(p))


def test_unsupported_and_missing():
    with pytest.raises(fh.ParseError):
        fh.parse_uploaded_file("x", "image/jpeg")
    with pytest.raises(fh.ParseError):
        fh.load_txt("/nonexistent/file.txt")


def test_txt_csv_ok(tmp_path):
    t = tmp_path / "a.txt"
    t.write_text("hello")
    assert fh.load_txt(str(t))[0].text == "hello"
    c = tmp_path / "a.csv"
    c.write_text("a,b\n1,2\n")
    assert fh.load_csv(str(c))[0].text == "a, b\n1, 2"


def test_docx_xlsx_roundtrip(tmp_path):
    import docx
    import openpyxl

    d = tmp_path / "a.docx"
    doc = docx.Document()
    doc.add_paragraph("para one")
    doc.save(str(d))
    assert "para one" in fh.load_docx(str(d))[0].text
    x = tmp_path / "a.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Sales"
    wb.active.append(["k", 5])
    wb.save(str(x))
    pg = fh.load_xlsx(str(x))[0]
    assert pg.sheet == "Sales" and "k, 5" in pg.text


def test_decompression_limit_uses_actual_size(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"\0" * 2_000_000)
    p = tmp_path / "bomb.docx"
    p.write_bytes(buf.getvalue())
    monkeypatch.setattr(fh, "MAX_DECOMPRESSED_BYTES", 100_000)
    with pytest.raises(fh.DecompressionLimitError):
        fh.load_docx(str(p))
    q = tmp_path / "bomb.xlsx"
    q.write_bytes(buf.getvalue())
    with pytest.raises(fh.DecompressionLimitError):
        fh.load_xlsx(str(q))


def test_upload_parse_error_is_reported_per_file(monkeypatch):
    import app.main as m

    with TestClient(m.app) as c:
        r = c.post(
            "/v1/documents",
            files={"files": ("a.pdf", b"%PDF-1.4 junk", "application/pdf")},
            headers={"X-User-Id": "u1"},
        )
        res = r.json()["results"][0]
        assert r.status_code == 200 and res["ok"] is False and res["error"]["code"] == "parse_error"


def test_models_fake_outside_pytest_raises(monkeypatch):
    monkeypatch.delitem(sys.modules, "pytest")
    with pytest.raises(RuntimeError, match="test-only"):
        models.check_fake_allowed(get_settings())


def test_healthz_fake_flag():
    import app.main as m

    with TestClient(m.app) as c:
        assert c.get("/healthz").json() == {"models_loaded": True, "fake": True}


def test_healthz_fake_false_with_real_loaders(monkeypatch):
    import app.main as m

    monkeypatch.setattr(
        m,
        "load_models",
        lambda s: models.load_models(s, lambda i: object(), lambda i: object()),
    )
    with TestClient(m.app) as c:
        assert c.get("/healthz").json() == {"models_loaded": True, "fake": False}


def test_second_lifespan_reloads_once_more(monkeypatch):
    import app.main as m

    n = {"e": 0}
    monkeypatch.setattr(
        m,
        "load_models",
        lambda s: models.load_models(
            s, lambda i: n.__setitem__("e", n["e"] + 1) or object(), lambda i: object()
        ),
    )
    with TestClient(m.app):
        pass
    assert not models.models_loaded()
    with TestClient(m.app) as c:
        c.get("/healthz")
    assert n["e"] == 2

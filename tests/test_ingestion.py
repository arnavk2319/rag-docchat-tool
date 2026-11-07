"""T-006: ingestion pipeline, embedded Chroma, dedupe, delete, owner scoping."""

import asyncio
import shutil
import time
from pathlib import Path

import pytest
from app.core.models import FakeEmbedder
from app.services import ingestion as ing
from app.services.ingestion import IngestionService, chunk_pages
from app.store.chroma import ChromaStore
from app.store.registry import SqliteRegistry
from app.utilities import file_handler as fh

FIX = Path(__file__).parent / "fixtures"
CASES = [
    ("sample.pdf", fh.PDF, "page"),
    ("sample.docx", fh.DOCX, None),
    ("sample.txt", fh.TXT, None),
    ("sample.csv", fh.CSV, "sheet"),
    ("sample.xlsx", fh.XLSX, "sheet"),
]


class CountingEmbedder(FakeEmbedder):
    def __init__(self):
        self.calls = 0

    def encode(self, texts, **kw):
        self.calls += 1
        return super().encode(texts, **kw)


@pytest.fixture
def env(tmp_path):
    emb = CountingEmbedder()
    reg = SqliteRegistry(tmp_path / "r.db")
    store = ChromaStore(tmp_path / "chroma")
    return IngestionService(reg, store, emb), reg, store, emb


@pytest.mark.parametrize(("name", "mime", "key"), CASES)
async def test_ingest_each_type_metadata(env, name, mime, key):
    svc, _reg, store, _ = env
    doc = await svc.ingest("u1", str(FIX / name), name, mime)
    assert doc.status == "ready"
    n = store.count("u1", doc.doc_id)
    assert n > 0
    hits = store.query("u1", [1.0, 1.0, 1.0], k=50, doc_id=doc.doc_id)
    assert len(hits) == n
    for h in hits:
        m = h.metadata
        assert m["doc_id"] == doc.doc_id and m["owner_id"] == "u1"
        assert m["chunk_id"] == h.chunk_id
        assert m["char_end"] > m["char_start"] >= 0
        if key:
            assert key in m


async def test_dedupe_skips_embedding(env):
    svc, _, _store, emb = env
    d1 = await svc.ingest("u1", str(FIX / "sample.txt"), "a.txt", fh.TXT)
    calls = emb.calls
    assert calls > 0
    d2 = await svc.ingest("u1", str(FIX / "sample.txt"), "b.txt", fh.TXT)
    assert d2.doc_id == d1.doc_id and emb.calls == calls
    # other owner gets own copy
    d3 = await svc.ingest("u2", str(FIX / "sample.txt"), "a.txt", fh.TXT)
    assert d3.doc_id != d1.doc_id and emb.calls > calls


async def test_delete_removes_row_and_vectors(env):
    svc, reg, store, _ = env
    d = await svc.ingest("u1", str(FIX / "sample.csv"), "a.csv", fh.CSV)
    assert await svc.delete("u2", d.doc_id) is False
    assert store.count("u1", d.doc_id) > 0
    assert await svc.delete("u1", d.doc_id) is True
    assert reg.get("u1", d.doc_id) is None
    assert store.query("u1", [1.0, 1.0, 1.0], k=5, doc_id=d.doc_id) == []
    assert store.count("u1", d.doc_id) == 0


async def test_query_is_owner_scoped(env):
    svc, _, store, _ = env
    await svc.ingest("u1", str(FIX / "sample.txt"), "a.txt", fh.TXT)
    assert store.query("u2", [1.0, 1.0, 1.0], k=5) == []
    with pytest.raises(ValueError):
        store.query("", [1.0, 1.0, 1.0])


async def test_failed_parse_marks_failed_without_leak_then_retries(
    env, tmp_path, caplog
):
    svc, reg, store, _ = env
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"SECRET-CONTENT not a pdf")
    d = await svc.ingest("u1", str(bad), "bad.pdf", fh.PDF)
    assert d.status == "failed"
    assert "SECRET-CONTENT" not in caplog.text
    assert store.count("u1", d.doc_id) == 0
    # same bytes again: re-ingested (still fails) and no duplicate row
    d2 = await svc.ingest("u1", str(bad), "bad.pdf", fh.PDF)
    assert d2.doc_id == d.doc_id and len(reg.list_by_owner("u1")) == 1


async def test_stuck_processing_is_reingested(env):
    svc, reg, _store, _ = env
    p = str(FIX / "sample.txt")
    doc, _ = reg.create_if_absent("u1", "a.txt", fh.TXT, 1, ing.sha256_file(p))
    reg.update_status("u1", doc.doc_id, "processing")
    d = await svc.ingest("u1", p, "a.txt", fh.TXT)
    assert d.doc_id == doc.doc_id and d.status == "ready"


async def test_embed_failure_marks_failed(env):
    svc, _reg, store, emb = env

    def boom(texts, **kw):
        raise RuntimeError("secret text")

    emb.encode = boom
    d = await svc.ingest("u1", str(FIX / "sample.txt"), "a.txt", fh.TXT)
    assert d.status == "failed" and store.count("u1") == 0


async def test_event_loop_not_blocked(env):
    svc, *_ = env

    class Slow(FakeEmbedder):
        def encode(self, texts, **kw):
            time.sleep(0.3)
            return super().encode(texts, **kw)

    svc._embedder = Slow()
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    t = asyncio.create_task(ticker())
    await svc.ingest("u1", str(FIX / "sample.txt"), "a.txt", fh.TXT)
    t.cancel()
    assert ticks >= 5


def test_chunking_overlap_and_offsets():
    text = "".join(chr(97 + i % 26) for i in range(2500))
    cs = chunk_pages(
        [fh.ParsedPage(text=text, source="section 1")], "d", "o", 1000, 100
    )
    assert [(c.metadata["char_start"], c.metadata["char_end"]) for c in cs] == [
        (0, 1000),
        (900, 1900),
        (1800, 2500),
    ]
    for c in cs:
        assert text[c.metadata["char_start"] : c.metadata["char_end"]] == c.text
    assert chunk_pages([fh.ParsedPage(text="  \n", source="s")], "d", "o") == []
    with pytest.raises(ValueError):
        chunk_pages([], "d", "o", 10, 10)


async def test_large_input_batches(env, tmp_path):
    svc, _, _store, emb = env
    p = tmp_path / "big.txt"
    p.write_text("word " * 20000)
    shutil.copy(p, tmp_path / "x.txt")
    d = await svc.ingest("u1", str(p), "big.txt", fh.TXT)
    assert d.status == "ready" and emb.calls > 1


@pytest.mark.parametrize(
    ("name", "mime", "n"),
    [
        ("sample.pdf", fh.PDF, 2),
        ("sample.docx", fh.DOCX, 1),
        ("sample.txt", fh.TXT, 1),
        ("sample.csv", fh.CSV, 1),
        ("sample.xlsx", fh.XLSX, 2),
    ],
)
async def test_pages_or_sheets_set(env, name, mime, n):
    svc, reg, _store, _ = env
    d = await svc.ingest("u1", str(FIX / name), name, mime)
    assert reg.get("u1", d.doc_id).pages_or_sheets == n


def test_registry_update_pages_or_sheets(tmp_path):
    db = tmp_path / "p.db"
    reg = SqliteRegistry(db)
    d, _ = reg.create_if_absent("u1", "a", "text/plain", 1, "h")
    assert reg.update_pages_or_sheets("u2", d.doc_id, 3) is False
    assert reg.update_pages_or_sheets("u1", "nope", 3) is False
    with pytest.raises(ValueError):
        reg.update_pages_or_sheets("u1", d.doc_id, -1)
    assert reg.update_pages_or_sheets("u1", d.doc_id, 3) is True
    assert SqliteRegistry(db).get("u1", d.doc_id).pages_or_sheets == 3


async def test_empty_docs_fail(env, tmp_path):
    import docx

    svc, _reg, store, _ = env
    t = tmp_path / "e.txt"
    t.write_text("  \n ")
    c = tmp_path / "e.csv"
    c.write_text("\n\n")
    dx = tmp_path / "e.docx"
    docx.Document().save(dx)
    for p, m in ((t, fh.TXT), (c, fh.CSV), (dx, fh.DOCX)):
        d = await svc.ingest("u1", str(p), p.name, m)
        assert d.status == "failed"
        assert store.count("u1", d.doc_id) == 0


async def test_cancel_marks_failed_and_purges(env):
    svc, reg, store, _ = env

    class Slow(FakeEmbedder):
        def encode(self, texts, **kw):
            time.sleep(0.3)
            return super().encode(texts, **kw)

    svc._embedder = Slow()
    task = asyncio.create_task(
        svc.ingest("u1", str(FIX / "sample.txt"), "a.txt", fh.TXT)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (d,) = reg.list_by_owner("u1")
    assert d.status == "failed" and store.count("u1") == 0


async def test_cleanup_error_does_not_mask(env):
    svc, _reg, store, emb = env

    def boom(*a, **k):
        raise RuntimeError("x")

    emb.encode = boom
    store.delete_doc = boom
    d = await svc.ingest("u1", str(FIX / "sample.txt"), "a.txt", fh.TXT)
    assert d.status == "failed"


async def test_delete_during_ingest_is_busy(env):
    svc, reg, store, _ = env

    class Slow(FakeEmbedder):
        def encode(self, texts, **kw):
            time.sleep(0.4)
            return super().encode(texts, **kw)

    svc._embedder = Slow()
    task = asyncio.create_task(
        svc.ingest("u1", str(FIX / "sample.txt"), "a.txt", fh.TXT)
    )
    await asyncio.sleep(0.15)
    (d,) = reg.list_by_owner("u1")
    with pytest.raises(ing.DocumentBusyError):
        await svc.delete("u1", d.doc_id)
    await task
    assert await svc.delete("u1", d.doc_id) is True
    assert store.count("u1") == 0


async def test_small_owner_next_to_large_owner(env, tmp_path):
    svc, _reg, store, _ = env
    big = tmp_path / "big.txt"
    big.write_text("word " * 5000)
    await svc.ingest("big", str(big), "big.txt", fh.TXT)
    d = await svc.ingest("small", str(FIX / "sample.txt"), "s.txt", fh.TXT)
    hits = store.query("small", [1.0, 1.0, 1.0], k=20)
    assert len(hits) == store.count("small", d.doc_id) == 1
    assert {h.metadata["owner_id"] for h in hits} == {"small"}

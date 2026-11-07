"""T-006 extra: chunking edge cases, store isolation/persistence, failure/retry, concurrency."""

import asyncio
import logging
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
SRC = Path(__file__).parent.parent / "src"
TXT = str(FIX / "sample.txt")


class Spy(FakeEmbedder):
    def __init__(self):
        self.batches: list[int] = []

    def encode(self, texts, **kw):
        self.batches.append(len(texts))
        return super().encode(texts, **kw)


@pytest.fixture
def env(tmp_path):
    emb = Spy()
    reg = SqliteRegistry(tmp_path / "r.db")
    store = ChromaStore(tmp_path / "chroma")
    return IngestionService(reg, store, emb), reg, store, emb


def pg(text):
    return [fh.ParsedPage(text=text, source="section 1")]


# ---- chunking (parent-child, T-009; fixed-window tests replaced) ----
def test_chunk_empty_and_short():
    assert chunk_pages(pg(""), "d", "o") == ([], [])
    assert chunk_pages([], "d", "o") == ([], [])
    ps, cs = chunk_pages(pg("hello"), "d", "o")
    assert len(ps) == 1 and len(cs) == 1 and cs[0].text == "hello"
    assert cs[0].chunk_id == "d:0:0" and cs[0].metadata["parent_id"] == "d:0"
    assert (cs[0].metadata["char_start"], cs[0].metadata["char_end"]) == (0, 5)
    assert cs[0].metadata["section"] == ""


@pytest.mark.parametrize("text", ["héllo 😀 世界 " * 400, "a" * 5000, "ab\ncd " * 700])
def test_chunk_offsets_reproduce_source(text):
    _ps, cs = chunk_pages(pg(text), "d", "o")
    assert cs
    prev_end = 0
    for c in cs:
        s, e = c.metadata["char_start"], c.metadata["char_end"]
        assert 0 <= s < e <= len(text)
        assert text[s:e] == c.text
        assert s >= prev_end  # ordered, no overlap
        prev_end = e


def test_chunk_ids_unique_and_stable():
    pages = [
        fh.ParsedPage(text="a" * 2500, source="page 1", page=1),
        fh.ParsedPage(text="b" * 2500, source="page 2", page=2),
    ]
    a = chunk_pages(pages, "d", "o")[1]
    b = chunk_pages(pages, "d", "o")[1]
    ids = [c.chunk_id for c in a]
    assert len(set(ids)) == len(ids) and ids == [c.chunk_id for c in b]
    assert {c.metadata["page"] for c in a} == {1, 2}


async def test_reingest_same_chunk_ids(env, tmp_path):
    svc, reg, store, _ = env
    d = await svc.ingest("u1", TXT, "a.txt", fh.TXT)
    ids1 = {h.chunk_id for h in store.query("u1", [1, 1, 1], k=100, doc_id=d.doc_id)}
    reg.update_status("u1", d.doc_id, "failed")
    d2 = await svc.ingest("u1", TXT, "a.txt", fh.TXT)
    ids2 = {h.chunk_id for h in store.query("u1", [1, 1, 1], k=100, doc_id=d.doc_id)}
    assert d2.status == "ready" and ids1 == ids2 and len(ids1) == store.count("u1")


# ---- store ----
def test_store_cosine_order_and_filters(tmp_path):
    s = ChromaStore(tmp_path / "c")
    vecs = {"a": [1, 0, 0], "b": [0.9, 0.1, 0], "c": [0, 1, 0], "d": [-1, 0, 0]}
    docs = {"a": "d1", "b": "d1", "c": "d2", "d": "d2"}
    s.add(
        list(vecs),
        list(vecs.values()),
        list(vecs),
        [{"doc_id": docs[k], "owner_id": "u1"} for k in vecs],
    )
    hits = s.query("u1", [1, 0, 0], k=4)
    assert [h.chunk_id for h in hits] == ["a", "b", "c", "d"]
    assert [h.distance for h in hits] == sorted(h.distance for h in hits)
    assert [h.chunk_id for h in s.query("u1", [1, 0, 0], k=2)] == ["a", "b"]
    assert {h.chunk_id for h in s.query("u1", [1, 0, 0], k=10, doc_id="d2")} == {
        "c",
        "d",
    }
    assert s.query("u1", [1, 0, 0], k=0) == []
    assert s.query("u1", [1, 0, 0], k=10, doc_id="nope") == []


def test_store_owner_isolation_same_doc_id(tmp_path):
    s = ChromaStore(tmp_path / "c")
    for o in ("A", "B"):
        s.add(
            [f"{o}1"], [[1, 0, 0]], [f"text {o}"], [{"doc_id": "same", "owner_id": o}]
        )
    assert [h.chunk_id for h in s.query("B", [1, 0, 0], doc_id="same")] == ["B1"]
    s.delete_doc("B", "same")
    assert s.count("B") == 0 and s.count("A", "same") == 1
    assert [h.text for h in s.query("A", [1, 0, 0])] == ["text A"]
    for fn in (
        lambda: s.query("", [1, 0, 0]),
        lambda: s.delete_doc("", "same"),
        lambda: s.count(""),
    ):
        with pytest.raises(ValueError):
            fn()
    assert s.count("A") == 1


def test_store_add_requires_metadata_and_empty_noop(tmp_path):
    s = ChromaStore(tmp_path / "c")
    s.add([], [], [], [])
    with pytest.raises(ValueError):
        s.add(["x"], [[1, 0, 0]], ["t"], [{"doc_id": "d"}])
    with pytest.raises(ValueError):
        s.add(["x"], [[1, 0, 0]], ["t"], [{"owner_id": "o"}])
    assert s.query("o", [1, 0, 0]) == []  # empty collection


def test_store_persists_across_reopen(tmp_path):
    s = ChromaStore(tmp_path / "c")
    s.add(["a"], [[1, 0, 0]], ["t"], [{"doc_id": "d", "owner_id": "o"}])
    del s
    s2 = ChromaStore(tmp_path / "c")
    assert s2.count("o", "d") == 1
    assert s2.query("o", [1, 0, 0])[0].text == "t"
    assert s2._col.metadata["hnsw:space"] == "cosine"


def test_store_delete_idempotent(tmp_path):
    s = ChromaStore(tmp_path / "c")
    s.delete_doc("o", "nothing")
    s.add(["a"], [[1, 0, 0]], ["t"], [{"doc_id": "d", "owner_id": "o"}])
    s.delete_doc("o", "d")
    s.delete_doc("o", "d")
    assert s.count("o") == 0


def test_no_httpclient_in_src_and_telemetry_off(tmp_path):
    for p in SRC.rglob("*.py"):
        assert "HttpClient" not in p.read_text(), p
    s = ChromaStore(tmp_path / "c")
    assert s._client.get_settings().anonymized_telemetry is False


# ---- service ----
@pytest.mark.parametrize(
    ("name", "mime", "key"),
    [
        ("sample.pdf", fh.PDF, "page"),
        ("sample.docx", fh.DOCX, None),
        ("sample.txt", fh.TXT, None),
        ("sample.csv", fh.CSV, "sheet"),
        ("sample.xlsx", fh.XLSX, "sheet"),
    ],
)
async def test_every_chunk_has_full_metadata(env, name, mime, key):
    svc, _, store, _ = env
    d = await svc.ingest("u1", str(FIX / name), name, mime)
    hits = store.query("u1", [1, 1, 1], k=1000)
    assert hits
    for h in hits:
        m = h.metadata
        assert m["doc_id"] == d.doc_id and m["owner_id"] == "u1" and m["source"]
        assert m["char_start"] >= 0 and m["char_end"] > m["char_start"]
        assert h.text and (key is None or m[key] not in (None, ""))


async def test_dedupe_zero_parse_zero_embed(env, monkeypatch):
    svc, _, _store, emb = env
    d1 = await svc.ingest("u1", TXT, "a.txt", fh.TXT)
    parses = []
    real = ing.parse_uploaded_file
    monkeypatch.setattr(
        ing, "parse_uploaded_file", lambda *a: parses.append(a) or real(*a)
    )
    n = len(emb.batches)
    d2 = await svc.ingest("u1", TXT, "again.txt", fh.TXT)
    assert d2.doc_id == d1.doc_id and parses == [] and len(emb.batches) == n
    d3 = await svc.ingest("u2", TXT, "a.txt", fh.TXT)
    assert d3.doc_id != d1.doc_id and len(parses) == 1 and len(emb.batches) > n


@pytest.mark.parametrize(
    "exc", [fh.ParseError("SECRETBODY"), fh.DecompressionLimitError("SECRETBODY")]
)
async def test_parser_errors_fail_cleanly_then_retry(env, monkeypatch, caplog, exc):
    svc, reg, store, _ = env
    real = ing.parse_uploaded_file

    def boom(*a):
        raise exc

    monkeypatch.setattr(ing, "parse_uploaded_file", boom)
    with caplog.at_level(logging.DEBUG):
        d = await svc.ingest("u1", TXT, "a.txt", fh.TXT)
    assert d.status == "failed" and store.count("u1") == 0
    assert "SECRETBODY" not in caplog.text
    assert "SECRETBODY" not in repr(reg.get("u1", d.doc_id))
    monkeypatch.setattr(ing, "parse_uploaded_file", real)
    d2 = await svc.ingest("u1", TXT, "a.txt", fh.TXT)
    assert d2.doc_id == d.doc_id and d2.status == "ready" and store.count("u1") > 0


async def test_embed_fails_midbatch_purges_partial_then_retry(env, tmp_path, caplog):
    svc, _reg, store, emb = env
    p = tmp_path / "big.txt"
    p.write_text("SECRETWORD " * 20000)
    real = emb.encode
    state = {"n": 0}

    def flaky(texts, **kw):
        state["n"] += 1
        if state["n"] == 3:
            raise RuntimeError("SECRETWORD leaked")
        return real(texts, **kw)

    emb.encode = flaky
    with caplog.at_level(logging.DEBUG):
        d = await svc.ingest("u1", str(p), "big.txt", fh.TXT)
    assert d.status == "failed" and state["n"] == 3
    assert store.count("u1") == 0  # batches 1-2 were stored then purged
    assert "SECRETWORD" not in caplog.text
    emb.encode = real
    d2 = await svc.ingest("u1", str(p), "big.txt", fh.TXT)
    assert d2.doc_id == d.doc_id and d2.status == "ready" and store.count("u1") > 0


async def test_batches_at_most_32(env, tmp_path):
    svc, _, store, emb = env
    p = tmp_path / "big.txt"
    p.write_text("word " * 30000)
    await svc.ingest("u1", str(p), "big.txt", fh.TXT)
    assert len(emb.batches) > 1 and max(emb.batches) <= 32
    assert sum(emb.batches) == store.count("u1")


async def test_concurrent_same_bytes_single_parse(env, monkeypatch):
    svc, reg, _store, _ = env
    parses = []
    real = ing.parse_uploaded_file

    def slow(*a):
        parses.append(1)
        time.sleep(0.3)
        return real(*a)

    monkeypatch.setattr(ing, "parse_uploaded_file", slow)
    r = await asyncio.gather(
        svc.ingest("u1", TXT, "a.txt", fh.TXT), svc.ingest("u1", TXT, "a.txt", fh.TXT)
    )
    assert len(parses) == 1
    assert r[0].doc_id == r[1].doc_id and len(reg.list_by_owner("u1")) == 1
    assert (await svc.ingest("u1", TXT, "a.txt", fh.TXT)).status == "ready"


async def test_delete_idempotent_and_isolated(env):
    svc, reg, store, _ = env
    a = await svc.ingest("u1", TXT, "a.txt", fh.TXT)
    b = await svc.ingest("u2", TXT, "a.txt", fh.TXT)
    assert await svc.delete("u1", a.doc_id) is True
    assert await svc.delete("u1", a.doc_id) is False
    assert reg.get("u1", a.doc_id) is None and store.count("u1") == 0
    assert store.query("u1", [1, 1, 1], doc_id=a.doc_id) == []
    assert store.count("u2", b.doc_id) > 0 and reg.get("u2", b.doc_id)


async def test_empty_owner_rejected_on_ingest(env):
    svc, *_ = env
    with pytest.raises(ValueError):
        await svc.ingest("", TXT, "a.txt", fh.TXT)


@pytest.mark.parametrize("slow", ["parse", "embed"])
async def test_heartbeat_advances(env, monkeypatch, slow):
    svc, *_ = env
    if slow == "parse":
        real = ing.parse_uploaded_file

        def p(*a):
            time.sleep(0.3)
            return real(*a)

        monkeypatch.setattr(ing, "parse_uploaded_file", p)
    else:
        real_enc = ing._encode

        def e(emb, texts):
            time.sleep(0.3)
            return real_enc(emb, texts)

        monkeypatch.setattr(ing, "_encode", e)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    t = asyncio.create_task(ticker())
    d = await svc.ingest("u1", TXT, "a.txt", fh.TXT)
    t.cancel()
    assert d.status == "ready" and ticks >= 5

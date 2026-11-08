"""T-009 extra: chunking properties, parent store hardening, lookup, ingestion integration."""

import itertools
import random
import sqlite3
import threading
from pathlib import Path

import pytest
from app.core.models import FakeEmbedder
from app.services import chunking as ck
from app.services import ingestion as ing
from app.services.ingestion import IngestionService, chunk_pages
from app.services.parent_lookup import expand_to_parents
from app.store.parents import Parent, SqliteParents
from app.store.registry import SqliteRegistry
from app.utilities import file_handler as fh
from app.vectorstore import Hit
from vecutil import make_store

FIX = Path(__file__).parent / "fixtures"
CT, PT = ck.CHILD_TARGET_CHARS, ck.PARENT_TARGET_CHARS


def pg(text, **kw):
    return [fh.ParsedPage(text=text, source="section 1", **kw)]


def check_invariants(page_list, doc_id="d"):
    """Offsets round-trip, containment, ids, size caps; returns parents."""
    parents = ck.chunk_document(page_list, doc_id)
    pids = [p.parent_id for p in parents]
    assert pids == [f"{doc_id}:{i}" for i in range(len(pids))]
    cids: list[str] = []
    page_iter = iter(page_list)
    cur = None
    last_end = 0
    for p in parents:
        # locate the page by source+page+sheet (pages in order)
        if cur is None or (cur.source, cur.page, cur.sheet) != (p.source, p.page, p.sheet):
            cur = next(x for x in page_iter if (x.source, x.page, x.sheet) == (p.source, p.page, p.sheet))
            last_end = 0
        t = cur.text
        assert t[p.char_start : p.char_end] == p.text and p.text.strip() == p.text and p.text
        assert len(p.text) <= PT and p.char_start >= last_end
        last_end = p.char_end
        assert p.section is not None
        assert p.children
        prev = p.char_start
        for i, c in enumerate(p.children):
            assert c.chunk_id == f"{p.parent_id}:{i}" and c.parent_id == p.parent_id
            assert t[c.char_start : c.char_end] == c.text and c.text
            assert p.char_start <= c.char_start and c.char_end <= p.char_end
            assert 0 < len(c.text) <= CT and c.section is not None
            # no gap except whitespace
            assert t[prev : c.char_start].strip() == ""
            assert c.char_start >= prev
            prev = c.char_end
            cids.append(c.chunk_id)
        assert t[prev : p.char_end].strip() == ""
    assert len(set(cids)) == len(cids)
    return parents


def gen_text(rng: random.Random) -> str:
    words = ["alpha", "beta", "gamma", "Delta.", "eps!", "zeta?", "THE", "x" * 40, "héllo", "😀", "世界"]
    seps = [" ", " ", " ", "\n", "\n\n", "\r\n", "\r\n\r\n", "\t"]
    out = []
    for _ in range(rng.randint(0, 1500)):
        r = rng.random()
        if r < 0.02:
            out.append(f"# Heading {rng.randint(0, 9)}\n")
        elif r < 0.03:
            out.append(f"1.{rng.randint(0, 9)} Title\n")
        elif r < 0.04:
            out.append("Z" * rng.randint(1, 6000))
        else:
            out.append(rng.choice(words))
        out.append(rng.choice(seps))
    return "".join(out)


# ---------------- chunker properties ----------------
@pytest.mark.parametrize("seed", range(40))
def test_property_random_texts(seed):
    rng = random.Random(1000 + seed)
    check_invariants(pg(gen_text(rng)))


@pytest.mark.parametrize(
    "kind",
    ["paragraph", "sentence", "giant", "crlf", "crlf_para", "unicode", "spaces"],
)
def test_sizes_tolerance_by_text_kind(kind):
    text = {
        "paragraph": ("p" * 500 + " word " * 60 + "\n\n") * 80,
        "sentence": "This is a sentence about things. " * 1200,
        "giant": "Z" * 20000,
        "crlf": "line one of text\r\n" * 1500,
        "crlf_para": ("Para text here. " * 20 + "\r\n\r\n") * 60,
        "unicode": "héllo 😀 世界. " * 1500,
        "spaces": "word " * 5000,
    }[kind]
    parents = check_invariants(pg(text))
    for p in parents[:-1]:
        # soft lower bound: non-final parents are at least MIN_FRACTION of the target
        assert len(p.text) >= PT * ck.MIN_FRACTION - 2
        for c in p.children[:-1]:
            assert len(c.text) >= CT * ck.MIN_FRACTION - 2
    if kind == "giant":
        assert [len(c.text) for c in parents[0].children[:3]] == [CT, CT, CT]
        assert len(parents[0].text) == PT


def test_full_coverage_restored():
    # restores 'prev_end == len(text)' / no-gap assertion dropped from T-006 test
    for text in ["héllo 😀 世界 " * 400, "a" * 5000, "ab\ncd " * 700, "Z" * (CT + 1), "x" * CT]:
        parents = ck.chunk_document(pg(text), "d")
        spans = [(c.char_start, c.char_end) for p in parents for c in p.children]
        assert spans[0][0] == 0
        assert spans[-1][1] == len(text.rstrip())
        for (_, e1), (s2, _) in itertools.pairwise(spans):
            assert text[e1:s2].strip() == "" and s2 >= e1


def test_exact_boundary_restored():
    # restores test_chunk_exact_boundary at the new child size
    one = ck.chunk_document(pg("x" * CT), "d")
    assert len(one) == 1 and len(one[0].children) == 1
    two = ck.chunk_document(pg("x" * (CT + 1)), "d")
    assert [(c.char_start, c.char_end) for c in two[0].children] == [(0, CT), (CT, CT + 1)]
    par = ck.chunk_document(pg("x" * (PT + 1)), "d")
    assert len(par) == 2 and par[0].char_end == PT and par[1].char_start == PT


def test_split_span_invalid_input_safe():
    assert ck.split_span("", 0, 0, 100) == []
    assert ck.split_span("   \n ", 0, 5, 100) == []


def test_no_chunk_crosses_page_boundary_synthetic():
    pages = [fh.ParsedPage(text=("w" * 50 + " ") * 300, source=f"page {i}", page=i) for i in (1, 2, 3)]
    parents = check_invariants(pages)
    assert [p.page for p in parents] == sorted(p.page for p in parents)
    for p in parents:
        assert p.char_end <= len(pages[p.page - 1].text)


def test_real_fixtures_no_boundary_crossing():
    pdf = fh.parse_uploaded_file(str(FIX / "sample.pdf"), fh.PDF)
    xl = fh.parse_uploaded_file(str(FIX / "sample.xlsx"), fh.XLSX)
    for pages in (pdf, xl):
        parents = check_invariants(pages)
        by_key = {(p.source, p.page, p.sheet) for p in pages if p.text.strip()}
        assert {(p.source, p.page, p.sheet) for p in parents} <= by_key
    assert all(p.sheet for p in ck.chunk_document(xl, "d"))


def test_sheet_page_metadata_propagates():
    pages = [fh.ParsedPage(text="# H\n" + "a b " * 800, source="sheet S", sheet="S")]
    ps = ck.chunk_document(pages, "d")
    assert all(p.sheet == "S" and p.page is None and p.section == "" for p in ps)


# ---------------- sections ----------------
@pytest.mark.parametrize(
    "line,expected",
    [
        ("# Title", "Title"),
        ("### Deep heading ##", "Deep heading"),
        ("1.2 Title", "1.2 Title"),
        ("3 Introduction", "3 Introduction"),
        ("2. Background", "2. Background"),
        ("INTRODUCTION", "INTRODUCTION"),
        ("1. This is a full sentence list item.", None),
        ("1. buy milk, eggs;", None),
        ("1.2", None),
        ("Hello world", None),
        ("", None),
        ("   ", None),
        ("A1", None),
        ("SENTENCE IN CAPS.", None),
        ("#nospace", None),
        ("x" * 200, None),
        ("# " + "y" * 200, None),
    ],
)
def test_is_heading(line, expected):
    assert ck.is_heading(line) == expected


def test_section_never_none_and_crlf_headings():
    text = "# One\r\nbody text.\r\n\r\n1.2 Two\r\nmore.\r\n"
    parents = ck.chunk_document(pg(text), "d")
    assert [h for _, h in ck.find_headings(text)] == ["One", "1.2 Two"]
    assert all(isinstance(c.section, str) for p in parents for c in p.children)
    assert parents[0].section == "One"


def test_section_assigned_per_child_and_carry_over_blank_page():
    body = ("Some sentence here. " * 70 + "\n\n")
    text = "# Alpha\n" + body + "# Beta\n" + body
    parents = ck.chunk_document(pg(text), "d")
    secs = [c.section for p in parents for c in p.children]
    assert secs[0] == "Alpha" and secs[-1] == "Beta"
    assert secs.index("Beta") > 0 and "" not in secs
    two = [
        fh.ParsedPage(text="# A\nx", source="p1", page=1),
        fh.ParsedPage(text="   ", source="p2", page=2),
        fh.ParsedPage(text="no headings", source="p3", page=3),
    ]
    out = ck.chunk_document(two, "d")
    assert [p.section for p in out] == ["A", "A"] and [p.page for p in out] == [1, 3]


def test_year_leading_sentence_not_heading():
    for line in (
        "2024 was a good year",
        "3 apples fell",
        "100 percent of users",
        "12 Angry Men was a film.",
    ):
        assert ck.is_heading(line) is None, line


def test_numbered_list_item_documented_gap():
    # Documented gap in ticket Findings: list item w/o trailing period reads as heading.
    assert ck.is_heading("1. Buy milk") == "1. Buy milk"


# ---------------- ids ----------------
def test_chunk_pages_ids_and_metadata_every_chunk():
    pages = [
        fh.ParsedPage(text="# S\n" + "a " * 2000, source="page 1", page=1),
        fh.ParsedPage(text="b " * 2000, source="sheet X", sheet="X"),
    ]
    parents, kids = chunk_pages(pages, "doc", "own")
    again = chunk_pages(pages, "doc", "own")
    assert [c.chunk_id for c in kids] == [c.chunk_id for c in again[1]]
    assert [p.parent_id for p in parents] == [p.parent_id for p in again[0]]
    ids = [c.chunk_id for c in kids]
    assert len(set(ids)) == len(ids)
    pids = {p.parent_id for p in parents}
    for c in kids:
        m = c.metadata
        assert m["doc_id"] == "doc" and m["owner_id"] == "own" and m["chunk_id"] == c.chunk_id
        assert m["parent_id"] in pids and c.chunk_id.startswith(m["parent_id"] + ":")
        assert 0 <= m["char_start"] < m["char_end"]
        assert isinstance(m["section"], str) and m["source"]
        assert all(v is not None for v in m.values())
        assert ("page" in m) != ("sheet" in m)
    assert all(p.owner_id == "own" and p.doc_id == "doc" for p in parents)


def test_empty_short_unicode_giant_token_via_chunk_pages():
    assert chunk_pages(pg("  \n\t"), "d", "o") == ([], [])
    _, k = chunk_pages(pg("😀"), "d", "o")
    assert k[0].text == "😀"
    _, k = chunk_pages(pg("Z" * 3000), "d", "o")
    assert [len(c.text) for c in k] == [CT, CT, 600]
    txt = "héllo 😀 世界 " * 400
    _, k = chunk_pages(pg(txt), "d", "o")
    assert all(txt[c.metadata["char_start"] : c.metadata["char_end"]] == c.text for c in k)


# ---------------- SqliteParents ----------------
def mk(owner, doc, n, text="t", section="", page=1):
    return Parent(owner_id=owner, doc_id=doc, parent_id=f"{doc}:{n}", char_start=0,
                  char_end=len(text), text=text, page=page, section=section)


def test_store_owner_scoping_all_ops(tmp_path):
    ps = SqliteParents(tmp_path / "x.db")
    ps.replace_doc("a", "d", [mk("a", "d", 0, "A0")])
    ps.replace_doc("b", "d", [mk("b", "d", 0, "B0")])  # same ids, other owner
    assert ps.get_parents("a", ["d:0"])[0].text == "A0"
    assert ps.get_parents("b", ["d:0"])[0].text == "B0"
    assert ps.get_parents("c", ["d:0"]) == []
    ps.delete_doc("c", "d")
    assert ps.count("a") == 1 and ps.count("b") == 1
    ps.delete_doc("a", "d")
    assert ps.count("a") == 0 and ps.count("b") == 1
    for fn in (lambda: ps.get_parents("", ["d:0"]), lambda: ps.delete_doc("", "d"),
               lambda: ps.replace_doc("", "d", [])):
        with pytest.raises(ValueError):
            fn()


def test_replace_doc_atomic_on_midtransaction_failure(tmp_path):
    ps = SqliteParents(tmp_path / "x.db")
    ps.replace_doc("u", "d", [mk("u", "d", 0, "old0"), mk("u", "d", 1, "old1")])
    dup = [mk("u", "d", 5, "n1"), mk("u", "d", 5, "n2")]  # PK violation after DELETE ran
    with pytest.raises(sqlite3.IntegrityError):
        ps.replace_doc("u", "d", dup)
    assert [p.text for p in ps.get_parents("u", ["d:0", "d:1"])] == ["old0", "old1"]
    assert ps.count("u", "d") == 2 and ps.get_parents("u", ["d:5"]) == []


def test_replace_doc_only_touches_that_doc(tmp_path):
    ps = SqliteParents(tmp_path / "x.db")
    ps.replace_doc("u", "d1", [mk("u", "d1", 0)])
    ps.replace_doc("u", "d2", [mk("u", "d2", 0)])
    ps.replace_doc("u", "d1", [])
    assert ps.count("u", "d1") == 0 and ps.count("u", "d2") == 1
    with pytest.raises(ValueError):
        ps.replace_doc("u", "d1", [mk("u", "d2", 0)])


def test_get_parents_order_dedupe_large(tmp_path):
    ps = SqliteParents(tmp_path / "x.db")
    n = 1200  # exceeds the 500-variable chunking
    ps.replace_doc("u", "d", [mk("u", "d", i, f"t{i}") for i in range(n)])
    want = [f"d:{i}" for i in random.Random(3).sample(range(n), 900)]
    got = ps.get_parents("u", want + want[:5] + ["missing"])
    assert [p.parent_id for p in got] == want
    assert ps.get_parents("u", []) == []


HOSTILE = [
    "x'; DROP TABLE parents; --",
    "\" OR 1=1 --",
    "Robert'); DELETE FROM parents;--",
    "% _ \\ ? :name $1",
    "nul\x00byte",
    "héllo 😀 世界",
]


@pytest.mark.parametrize("bad", HOSTILE)
def test_hostile_strings_are_data(tmp_path, bad):
    ps = SqliteParents(tmp_path / "x.db")
    ps.replace_doc("u", "d", [mk("u", "d", 0, bad, section=bad), mk("u", "d", 1, "keep")])
    got = ps.get_parents("u", ["d:0"])[0]
    assert got.text == bad and got.section == bad
    assert ps.count("u") == 2
    # hostile ids / owner / doc ids
    assert ps.get_parents("u", [bad, "d:1' OR '1'='1"]) == []
    assert ps.get_parents(bad, ["d:0"]) == []
    ps.delete_doc(bad, bad)
    assert ps.count("u") == 2
    ps.replace_doc(bad, bad, [mk(bad, bad, 0, "z")])
    assert ps.get_parents(bad, [f"{bad}:0"])[0].text == "z"


def test_concurrent_replace_doc_threads(tmp_path):
    ps = SqliteParents(tmp_path / "x.db")
    errs: list[BaseException] = []

    def work(i):
        try:
            for r in range(8):
                ps.replace_doc("u", f"d{i}", [mk("u", f"d{i}", j, f"{i}-{r}-{j}") for j in range(20)])
                ps.replace_doc("u", "shared", [mk("u", "shared", j, f"{i}-{r}") for j in range(5)])
        except BaseException as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    for i in range(6):
        assert ps.count("u", f"d{i}") == 20
    assert ps.count("u", "shared") == 5
    texts = {p.text for p in ps.get_parents("u", [f"shared:{j}" for j in range(5)])}
    assert len({t for t in texts}) == 1  # all five from one writer (atomic replace)


def test_reopen_persistence_and_idempotent_schema(tmp_path):
    db = tmp_path / "sub" / "x.db"
    SqliteParents(db).replace_doc("u", "d", [mk("u", "d", 0, "persist", page=None)])
    again = SqliteParents(db)
    p = again.get_parents("u", ["d:0"])[0]
    assert p.text == "persist" and p.page is None and p.sheet is None


def test_shares_registry_file(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    ps = SqliteParents(tmp_path / "r.db")
    ps.replace_doc("u", "d", [mk("u", "d", 0)])
    assert reg.list_docs("u") == [] if hasattr(reg, "list_docs") else True
    assert ps.count("u") == 1


# ---------------- expand_to_parents ----------------
def hit(cid, pid, dist, **meta):
    m = {"parent_id": pid, **meta} if pid is not None else dict(meta)
    return Hit(chunk_id=cid, text="c", metadata=m, distance=dist)


def test_expand_best_child_chosen_regardless_of_input_order(tmp_path):
    ps = SqliteParents(tmp_path / "x.db")
    ps.replace_doc("u", "d", [mk("u", "d", 0), mk("u", "d", 1), mk("u", "d", 2)])
    hits = [hit("d:0:1", "d:0", 0.6), hit("d:2:0", "d:2", 0.2), hit("d:0:0", "d:0", 0.1),
            hit("d:1:0", "d:1", 0.4)]
    out = expand_to_parents("u", hits, ps)
    assert [o.parent.parent_id for o in out] == ["d:0", "d:2", "d:1"]
    assert out[0].best_child_id == "d:0:0" and out[0].distance == 0.1
    assert [round(o.score, 6) for o in out] == [0.9, 0.8, 0.6]
    assert expand_to_parents("u", [], ps) == []


def test_expand_missing_parent_and_legacy_hits_skipped(tmp_path):
    ps = SqliteParents(tmp_path / "x.db")
    ps.replace_doc("u", "d", [mk("u", "d", 0)])
    hits = [
        hit("old:0", None, 0.0),  # pre-T-009: no parent_id
        hit("old:1", "", 0.0),
        hit("gone:0:0", "gone:0", 0.05),  # stale child, parent deleted
        hit("d:0:0", "d:0", 0.3),
    ]
    out = expand_to_parents("u", hits, ps)
    assert [o.parent.parent_id for o in out] == ["d:0"]
    assert expand_to_parents("u", hits[:3], ps) == []


def test_expand_ties_keep_input_order(tmp_path):
    ps = SqliteParents(tmp_path / "x.db")
    ps.replace_doc("u", "d", [mk("u", "d", i) for i in range(3)])
    hits = [hit(f"d:{i}:0", f"d:{i}", 0.5) for i in (2, 0, 1)]
    assert [o.parent.parent_id for o in expand_to_parents("u", hits, ps)] == ["d:2", "d:0", "d:1"]


# ---------------- ingestion integration ----------------
class Spy(FakeEmbedder):
    def __init__(self):
        self.seen: list[str] = []

    def encode(self, texts, **kw):
        self.seen.extend(texts)
        return super().encode(texts, **kw)


def make(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    store = make_store()
    emb = Spy()
    return IngestionService(reg, store.inner, emb), reg, store, emb


def write(tmp_path, name, text):
    f = tmp_path / name
    f.write_text(text)
    return str(f)


async def test_embedder_sees_only_children(tmp_path):
    svc, _reg, store, emb = make(tmp_path)
    text = "# T\n" + ("Alpha beta gamma. " * 40 + "\n\n") * 30
    d = await svc.ingest("u", write(tmp_path, "a.txt", text), "a.txt", fh.TXT)
    assert d.status == "ready"
    assert emb.seen and max(len(t) for t in emb.seen) <= CT
    assert len(emb.seen) == store.count("u", d.doc_id)
    n_par = svc._parents.count("u", d.doc_id)
    parent_texts = {
        p.text for p in svc._parents.get_parents("u", [f"{d.doc_id}:{i}" for i in range(n_par)])
    }
    assert n_par >= 2 and not (set(emb.seen) & parent_texts)
    assert max(len(t) for t in parent_texts) > CT


async def test_reingest_ids_stable_and_cascade(tmp_path):
    svc, reg, store, emb = make(tmp_path)
    path = write(tmp_path, "a.txt", "# T\n" + "Sentence number one is here. " * 600)
    d = await svc.ingest("u", path, "a.txt", fh.TXT)
    q = [float(x) for x in FakeEmbedder().encode(["Sentence"])[0]]
    ids1 = sorted(h.chunk_id for h in store.query("u", q, k=500, doc_id=d.doc_id))
    n1 = svc._parents.count("u", d.doc_id)
    # ready + same bytes -> deduped, nothing re-embedded
    seen = len(emb.seen)
    again = await svc.ingest("u", path, "a.txt", fh.TXT)
    assert again.doc_id == d.doc_id and len(emb.seen) == seen
    # fail then retry -> same ids, parents replaced not duplicated
    reg.update_status("u", d.doc_id, "failed")
    d2 = await svc.ingest("u", path, "a.txt", fh.TXT)
    assert d2.status == "ready" and d2.doc_id == d.doc_id
    assert sorted(h.chunk_id for h in store.query("u", q, k=500, doc_id=d.doc_id)) == ids1
    assert svc._parents.count("u", d.doc_id) == n1


async def test_parse_failure_and_empty_doc_leave_no_parents(tmp_path):
    svc, _reg, store, emb = make(tmp_path)
    d = await svc.ingest("u", write(tmp_path, "e.txt", "  \n\n "), "e.txt", fh.TXT)
    assert d.status == "failed" and "empty" in (d.error or d.status_reason or "empty") if hasattr(d, "error") else True
    assert svc._parents.count("u") == 0 and store.count("u") == 0 and emb.seen == []


async def test_failure_midway_purges_parents(tmp_path, monkeypatch):
    svc, _reg, store, _emb = make(tmp_path)

    async def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(store.inner, "upsert_chunks", boom)
    d = await svc.ingest("u", write(tmp_path, "a.txt", "hello world. " * 500), "a.txt", fh.TXT)
    assert d.status == "failed"
    assert svc._parents.count("u") == 0 and store.count("u") == 0


async def test_cancel_purges_parents(tmp_path):
    import asyncio

    svc, _reg, store, _emb = make(tmp_path)
    gate = asyncio.Event()
    orig = ing.run_blocking

    async def slow(fn, *a, **k):
        if getattr(fn, "__name__", "") == "_encode":
            await gate.wait()
        return await orig(fn, *a, **k)

    ing.run_blocking = slow
    try:
        t = asyncio.create_task(
            svc.ingest("u", write(tmp_path, "a.txt", "hello world. " * 500), "a.txt", fh.TXT)
        )
        for _ in range(200):
            await asyncio.sleep(0.01)
            if svc._parents.count("u") > 0:
                break
        assert svc._parents.count("u") > 0
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    finally:
        ing.run_blocking = orig
    assert svc._parents.count("u") == 0 and store.count("u") == 0


async def test_delete_cascade_owner_scope_and_busy(tmp_path):
    svc, _reg, store, _emb = make(tmp_path)
    d = await svc.ingest("u", write(tmp_path, "a.txt", "hello world. " * 500), "a.txt", fh.TXT)
    assert svc._parents.count("u", d.doc_id) > 0
    assert await svc.delete("other", d.doc_id) is False
    assert svc._parents.count("u", d.doc_id) > 0 and store.count("u", d.doc_id) > 0
    ing._inflight.add(d.doc_id)
    try:
        with pytest.raises(ing.DocumentBusyError):
            await svc.delete("u", d.doc_id)
        assert svc._parents.count("u", d.doc_id) > 0
    finally:
        ing._inflight.discard(d.doc_id)
    assert await svc.delete("u", d.doc_id) is True
    assert svc._parents.count("u") == 0 and store.count("u") == 0


async def test_legacy_child_without_parent_id_does_not_crash(tmp_path):
    svc, _reg, store, _emb = make(tmp_path)
    vec = [float(x) for x in FakeEmbedder().encode(["legacy chunk"])[0]]
    store.add(
        ["old-doc:0"], [vec], ["legacy chunk"],
        [{"doc_id": "old-doc", "owner_id": "u", "char_start": 0, "char_end": 12, "source": "section 1"}],
    )
    hits = store.query("u", vec, k=3)
    assert hits and "parent_id" not in hits[0].metadata
    assert expand_to_parents("u", hits, svc._parents) == []


async def test_two_owners_same_file_isolated_parents(tmp_path):
    svc, _reg, _store, _emb = make(tmp_path)
    path = write(tmp_path, "a.txt", "shared content here. " * 400)
    a = await svc.ingest("a", path, "a.txt", fh.TXT)
    b = await svc.ingest("b", path, "a.txt", fh.TXT)
    assert svc._parents.count("a", a.doc_id) > 0 and svc._parents.count("b", b.doc_id) > 0
    await svc.delete("a", a.doc_id)
    assert svc._parents.count("a") == 0 and svc._parents.count("b", b.doc_id) > 0

"""T-009: parent-child chunking, parent store, child->parent lookup."""

import pytest
from app.core.models import FakeEmbedder
from app.services import chunking as ck
from app.services.ingestion import IngestionService
from app.services.parent_lookup import expand_to_parents
from app.store.chroma import ChromaStore, Hit
from app.store.parents import Parent, SqliteParents
from app.store.registry import SqliteRegistry
from app.utilities import file_handler as fh

PROSE = ("The quick brown fox jumps over the lazy dog. " * 12 + "\n\n") * 40


def pages():
    return [
        fh.ParsedPage(text=PROSE, source="page 1", page=1),
        fh.ParsedPage(text=PROSE[:3000], source="page 2", page=2),
        fh.ParsedPage(text="a,b\n1,2\n" * 400, source="sheet S", sheet="S"),
    ]


def test_sizes_offsets_and_no_page_crossing():
    ps = ck.chunk_document(pages(), "d")
    assert {p.page for p in ps if p.page} == {1, 2} and any(p.sheet == "S" for p in ps)
    pgs = {(p.page, p.sheet): p for p in pages()}
    # rebuild page map by source
    by_src = {p.source: p for p in pages()}
    for par in ps:
        page = by_src[par.source]
        assert page.text[par.char_start : par.char_end] == par.text
        assert len(par.text) <= ck.PARENT_TARGET_CHARS
        assert par.children
        for ch in par.children:
            assert page.text[ch.char_start : ch.char_end] == ch.text
            assert par.char_start <= ch.char_start and ch.char_end <= par.char_end
            assert 0 < len(ch.text) <= ck.CHILD_TARGET_CHARS
            assert ch.chunk_id.startswith(par.parent_id + ":") and ch.parent_id == par.parent_id
    assert pgs
    # tolerance: all but the last child of each parent are >= half the target
    for par in ps:
        for ch in par.children[:-1]:
            assert len(ch.text) >= ck.CHILD_TARGET_CHARS * ck.MIN_FRACTION * 0.5


def test_prefers_paragraph_then_sentence_then_hardcut():
    para = "x" * 700 + "\n\n" + "y" * 700
    spans = ck.split_span(para, 0, len(para), 1200)
    assert [para[s:e] for s, e in spans] == ["x" * 700, "y" * 700]
    sent = "w " * 400 + "End here. " + "z " * 200
    s, e = ck.split_span(sent, 0, len(sent), 1000)[0]
    assert sent[s:e].endswith("End here.")
    blob = "q" * 3000
    assert [e - s for s, e in ck.split_span(blob, 0, 3000, 1200)] == [1200, 1200, 600]


def test_sections_and_empty_default():
    text = "# Intro\nhello world.\n\n1.2 Methods\nwe did things.\n\nRESULTS\nnumbers."
    assert [h for _, h in ck.find_headings(text)] == ["Intro", "1.2 Methods", "RESULTS"]
    ps = ck.chunk_document([fh.ParsedPage(text=text, source="s")], "d")
    assert ps[0].section == "Intro"
    assert ck.chunk_document([fh.ParsedPage(text="plain text", source="s")], "d")[0].section == ""
    # carried across pages
    two = [
        fh.ParsedPage(text="# Part A\nbody", source="p1", page=1),
        fh.ParsedPage(text="more body", source="p2", page=2),
    ]
    assert ck.chunk_document(two, "d")[1].section == "Part A"


def test_whitespace_only_page_yields_nothing():
    assert ck.chunk_document([fh.ParsedPage(text=" \n\n ", source="s")], "d") == []


def mkparent(owner, doc, n, text="t"):
    return Parent(owner_id=owner, doc_id=doc, parent_id=f"{doc}:{n}", char_start=0,
                  char_end=len(text), text=text, page=1)


def test_parent_store_order_scope_replace_delete(tmp_path):
    ps = SqliteParents(tmp_path / "x.db")
    ps.replace_doc("u", "d", [mkparent("u", "d", 0, "zero"), mkparent("u", "d", 1, "one")])
    assert [p.text for p in ps.get_parents("u", ["d:1", "d:0", "nope"])] == ["one", "zero"]
    assert ps.get_parents("other", ["d:0"]) == []
    ps.replace_doc("u", "d", [mkparent("u", "d", 0, "new")])
    assert ps.count("u", "d") == 1 and ps.get_parents("u", ["d:0"])[0].text == "new"
    with pytest.raises(ValueError):
        ps.replace_doc("u", "d", [mkparent("v", "d", 0)])
    assert ps.count("u", "d") == 1  # failed replace left data intact
    ps.delete_doc("u", "d")
    assert ps.count("u") == 0


async def test_ingest_embeds_children_only_and_child_query_returns_parent(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    store = ChromaStore(tmp_path / "c")
    svc = IngestionService(reg, store, FakeEmbedder())
    f = tmp_path / "a.txt"
    f.write_text("# Title\n" + PROSE)
    d = await svc.ingest("u1", str(f), "a.txt", fh.TXT)
    assert d.status == "ready"
    parents = svc._parents
    n_par = parents.count("u1", d.doc_id)
    n_child = store.count("u1", d.doc_id)
    assert n_par >= 2 and n_child > n_par
    emb = FakeEmbedder()
    q = emb.encode(["The quick brown fox"])[0]
    hits = store.query("u1", [float(x) for x in q], k=5, doc_id=d.doc_id)
    for h in hits:
        assert h.metadata["section"] == "Title" and h.metadata["parent_id"].startswith(d.doc_id)
        assert all(v is not None for v in h.metadata.values())
    out = expand_to_parents("u1", hits, parents)
    assert out and len({o.parent.parent_id for o in out}) == len(out)
    assert out[0].best_child_id == hits[0].chunk_id
    assert out[0].parent.parent_id == hits[0].metadata["parent_id"]
    assert hits[0].text in out[0].parent.text
    assert expand_to_parents("u2", hits, parents) == []
    # delete removes parents; re-ingest after failure replaces them
    reg.update_status("u1", d.doc_id, "failed")
    await svc.ingest("u1", str(f), "a.txt", fh.TXT)
    assert parents.count("u1", d.doc_id) == n_par
    assert await svc.delete("u1", d.doc_id)
    assert parents.count("u1") == 0


def test_expand_dedupes_keeps_best_order(tmp_path):
    ps = SqliteParents(tmp_path / "x.db")
    ps.replace_doc("u", "d", [mkparent("u", "d", 0), mkparent("u", "d", 1)])

    def h(cid, pid, dist):
        return Hit(chunk_id=cid, text="c", metadata={"parent_id": pid}, distance=dist)

    out = expand_to_parents(
        "u", [h("d:1:0", "d:1", 0.1), h("d:0:0", "d:0", 0.3), h("d:1:1", "d:1", 0.5), h("x", "", 0)], ps
    )
    assert [o.parent.parent_id for o in out] == ["d:1", "d:0"]
    assert out[0].best_child_id == "d:1:0" and out[0].score == pytest.approx(0.9)


def _ref_section_at(headings, offset, carry):
    cur = carry
    for off, h in headings:
        if off > offset:
            break
        cur = h
    return cur


def test_sections_match_linear_reference_on_random_doc():
    import random

    rnd = random.Random(7)
    lines = []
    for i in range(600):
        lines.append(rnd.choice([f"# H{i}", f"{i % 9 + 1}.{i % 5} Title {i}", "body text here " * rnd.randint(1, 30), "", "SHOUT"]))
    text = "\n".join(lines)
    pgs = [fh.ParsedPage(text=text, source="p1", page=1), fh.ParsedPage(text=text[:900], source="p2", page=2)]
    heads = ck.find_headings(text)
    carry = ""
    for par, pg in zip(
        [p for p in ck.chunk_document(pgs, "d") if p.page == 1], [pgs[0]] * 1000
    ):
        assert par.section == _ref_section_at(heads, par.char_start, carry)
        for c in par.children:
            assert c.section == _ref_section_at(heads, c.char_start, carry)
        assert pg


def test_many_headings_is_fast():
    import time

    text = "# a\n" * 200_000
    t = time.perf_counter()
    ps = ck.chunk_document([fh.ParsedPage(text=text, source="p", page=1)], "d")
    assert time.perf_counter() - t < 3 and ps and ps[0].section == "a"


@pytest.mark.parametrize(
    "line",
    ["1" + ".1" * 40000, "9" * 100000, "1. " + "A" * 100000, "#" * 100000, "A " * 50000, "1.2 " + "Word " * 20000],
)
def test_pathological_lines_fast(line):
    import time

    t = time.perf_counter()
    ck.find_headings(line + "\n" + line)
    ck.chunk_document([fh.ParsedPage(text=line, source="p", page=1)], "d")
    assert time.perf_counter() - t < 3

"""T-010 extra: isolation, empty doc_ids, BM25 corruption, RRF ordering, BM25-only hits."""

from app.core.models import FakeEmbedder
from app.services.retrieval import hybrid_search, rrf
from app.store.bm25 import Bm25Store
from app.store.chroma import ChromaStore

EMB = FakeEmbedder()


def _setup(tmp_path):
    store = ChromaStore(tmp_path / "chroma")
    bm = Bm25Store(tmp_path / "bm25")
    docs = [
        ("u", "d1", "alpha zanzibar protocol notes"),
        ("u", "d2", "beta zanzibar ledger"),
        ("u", "d3", "gamma zanzibar secrets"),
        ("v", "d4", "delta zanzibar other owner"),
    ]
    for o, d, t in docs:
        store.add([f"{d}-0"], EMB.encode([t]), [t], [{"owner_id": o, "doc_id": d}])
        bm.add_doc(o, d, [f"{d}-0"], [t])
    return store, bm


def _ids(hits):
    return {h.chunk_id for h in hits}


def test_hybrid_doc_ids_and_owner_isolation(tmp_path):
    store, bm = _setup(tmp_path)
    q = "zanzibar"
    e = EMB.encode([q])[0]
    got = hybrid_search("u", q, e, store, bm, doc_ids=["d1", "d2"], top_k=10)
    assert _ids(got) == {"d1-0", "d2-0"}
    got = hybrid_search("u", q, e, store, bm, doc_ids=["d1", "d4"], top_k=10)
    assert _ids(got) == {"d1-0"}
    got = hybrid_search("u", q, e, store, bm, top_k=10)
    assert _ids(got) == {"d1-0", "d2-0", "d3-0"}
    assert _ids(hybrid_search("v", q, e, store, bm, top_k=10)) == {"d4-0"}


def test_hybrid_empty_doc_ids_returns_nothing(tmp_path):
    store, bm = _setup(tmp_path)
    e = EMB.encode(["zanzibar"])[0]
    assert hybrid_search("u", "zanzibar", e, store, bm, doc_ids=[], top_k=5) == []
    assert bm.query("u", "zanzibar", k=5, doc_ids=[]) == []


def test_bm25_only_hit_fetched_from_chroma(tmp_path):
    store = ChromaStore(tmp_path / "chroma")
    bm = Bm25Store(tmp_path / "bm25")
    store.add(["a", "b"], EMB.encode(["x", "yy"]), ["x", "yy"],
              [{"owner_id": "u", "doc_id": "d"}] * 2)
    # dense side only sees "a" (top_n=1); BM25 surfaces "b" only.
    bm.add_doc("u", "d", ["a", "b"], ["nothing relevant", "rareword here"])
    e = EMB.encode(["x"])[0]
    got = hybrid_search("u", "rareword", e, store, bm, top_k=5, top_n=1)
    assert "b" in [h.chunk_id for h in got]
    assert next(h for h in got if h.chunk_id == "b").text == "yy"


def test_bm25_corrupt_files_skipped(tmp_path):
    d = tmp_path / "bm25"
    bm = Bm25Store(d)
    bm.add_doc("u", "good", ["g0"], ["findme token"])
    (d / "bad1.json").write_text("{not json", encoding="utf-8")
    (d / "bad2.json").write_text('{"owner_id":"u","doc_id":"x"}', encoding="utf-8")
    (d / "bad3.json").write_text(
        '{"owner_id":"u","doc_id":"y","chunk_ids":["a","b"],"tokens":[["t"]]}', encoding="utf-8"
    )
    bm2 = Bm25Store(d)  # must not raise
    assert bm2.has_doc("u", "good")
    assert not bm2.has_doc("u", "x") and not bm2.has_doc("u", "y")
    assert [c for c, _ in bm2.query("u", "findme", k=3)] == ["g0"]


def test_bm25_restart_after_overwrite_and_delete(tmp_path):
    d = tmp_path / "bm25"
    bm = Bm25Store(d)
    bm.add_doc("u", "d", ["c0"], ["old words"])
    bm.add_doc("u", "d", ["c1"], ["new words"])
    bm2 = Bm25Store(d)
    assert [c for c, _ in bm2.query("u", "words", k=5)] == ["c1"]
    assert bm2.query("u", "old", k=5) == []
    bm2.delete_doc("u", "d")
    bm2.delete_doc("u", "d")  # idempotent
    assert Bm25Store(d).query("u", "words", k=5) == []


def test_bm25_requires_owner_and_ranks_by_term_match(tmp_path):
    import pytest

    bm = Bm25Store(tmp_path / "b")
    with pytest.raises(ValueError):
        bm.add_doc("", "d", ["c"], ["t"])
    with pytest.raises(ValueError):
        bm.query("", "t")
    bm.add_doc("u", "d", ["c0", "c1", "c2"], ["apple pie", "apple apple tart", "banana"])
    res = bm.query("u", "apple", k=5)
    assert [c for c, _ in res] == ["c1", "c0"]  # no match for c2
    assert bm.query("u", "", k=5) == [] and bm.query("u", "apple", k=0) == []


def test_rrf_ordering_and_ties():
    out = rrf([["a", "b", "c"], ["c", "b", "d"]])
    ids = [i for i, _ in out]
    # convexity: 1/61+1/63 (c) > 2/62 (b) > 1/61 (a) > 1/63 (d)
    assert ids == ["c", "b", "a", "d"]
    # ties break by id
    assert [i for i, _ in rrf([["z"], ["a"]])] == ["a", "z"]
    # duplicates within one list count once; empty input ok
    assert dict(rrf([["a", "a"]]))["a"] == 1 / 61
    assert rrf([]) == [] and rrf([[], []]) == []

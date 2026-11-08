"""T-010: BM25 store, RRF hybrid retrieval, doc_ids filter, eval line."""

from app.core.models import FakeEmbedder
from app.services.ingestion import IngestionService
from app.services.retrieval import hybrid_search, rrf
from app.store.bm25 import Bm25Store
from app.store.registry import SqliteRegistry
from vecutil import make_store

EMB = FakeEmbedder()

# 8 chunks, each with a distinctive keyword; 8 questions each targeting one chunk.
MINI = [
    ("c0", "The invoice number is INV-4471 and payment is due in thirty days."),
    ("c1", "Residence Inn Toronto Vaughan confirmation reservation number 84176338."),
    ("c2", "Pack the passport, charger, and adapter before the flight to Lisbon."),
    ("c3", "Quarterly revenue grew twelve percent driven by subscription renewals."),
    ("c4", "Photosynthesis converts sunlight into glucose inside chloroplasts."),
    ("c5", "The warranty covers manufacturing defects for twenty four months."),
    ("c6", "Kubernetes schedules containers across a cluster of worker nodes."),
    ("c7", "Marathon training plan peaks with a thirty kilometer long run."),
]
QUESTIONS = [
    ("What is the invoice number INV-4471?", "c0"),
    ("Which reservation number at Residence Inn Toronto?", "c1"),
    ("What to pack for Lisbon, passport and adapter?", "c2"),
    ("How much did subscription revenue grow?", "c3"),
    ("Where does photosynthesis happen, chloroplasts?", "c4"),
    ("How long does the warranty cover defects?", "c5"),
    ("What does Kubernetes schedule on worker nodes?", "c6"),
    ("When does the marathon long run peak?", "c7"),
]


def _build(tmp_path, owner="u"):
    store = make_store()
    bm = Bm25Store()
    ids = [c for c, _ in MINI]
    texts = [t for _, t in MINI]
    store.add(ids, EMB.encode(texts), texts, [{"owner_id": owner, "doc_id": "d1"} for _ in ids])
    bm.add_doc(owner, "d1", ids, texts)
    return store, bm


def test_rrf_basic():
    out = dict(rrf([["a", "b"], ["b", "c"]]))
    assert out["b"] > out["a"] > out["c"]


def _recall(tmp_path, use_bm25):
    store, bm = _build(tmp_path)
    ok = 0
    for q, want in QUESTIONS:
        hits = hybrid_search("u", q, EMB.encode([q])[0], store, bm if use_bm25 else None,
                             top_k=5)
        ok += want in [h.chunk_id for h in hits]
    return ok / len(QUESTIONS)


def test_eval_hybrid_recall_at_5(tmp_path):
    hybrid = _recall(tmp_path / "h", True)
    dense = _recall(tmp_path / "d", False)
    print(f"recall@5 hybrid={hybrid:.2f} dense={dense:.2f}")
    assert hybrid >= 0.8
    assert hybrid >= dense


def test_bm25_rebuild_from_store(tmp_path):
    """A fresh Bm25Store (restart / second replica) rebuilds from the chunk text in the VectorStore."""
    store, bm = _build(tmp_path)
    before = hybrid_search("u", "invoice INV-4471", EMB.encode(["x"])[0], store, bm, top_k=5)
    fresh = Bm25Store()
    assert not fresh.has_owner("u") and fresh.query("u", "invoice", k=5) == []
    after = hybrid_search("u", "invoice INV-4471", EMB.encode(["x"])[0], store, fresh, top_k=5)
    assert fresh.has_owner("u")
    assert [h.chunk_id for h in after] == [h.chunk_id for h in before]
    assert fresh.query("u", "invoice INV-4471", k=5) == bm.query("u", "invoice INV-4471", k=5)
    assert fresh.query("u", "invoice INV-4471", k=5)[0][0] == "c0"
    assert fresh.query("other", "invoice", k=5) == []


def test_bm25_delete(tmp_path):
    _, bm = _build(tmp_path)
    bm.delete_doc("u", "d1")
    assert bm.query("u", "invoice", k=5) == []


def test_doc_ids_filter_two_of_three_and_owner(tmp_path):
    store = make_store()
    for owner, doc in [("u", "d1"), ("u", "d2"), ("u", "d3"), ("v", "d4")]:
        store.add([f"{doc}-0"], EMB.encode(["x"]), ["x"], [{"owner_id": owner, "doc_id": doc}])
    v = EMB.encode(["x"])[0]
    got = store.query("u", v, k=10, doc_ids=["d1", "d2"])
    assert {h.chunk_id for h in got} == {"d1-0", "d2-0"}
    assert {h.chunk_id for h in store.query("u", v, k=10, doc_ids=["d1", "d4"])} == {"d1-0"}
    assert store.query("u", v, k=10, doc_ids=[]) == []
    assert len(store.query("u", v, k=10)) == 3  # owner-wide still works
    assert len(store.query("u", v, k=10, doc_id="d3")) == 1  # single form
    assert store.get("u", ["d4-0", "d1-0"])[0].chunk_id == "d1-0"


async def test_ingest_indexes_and_delete_removes(tmp_path):
    from pathlib import Path

    reg = SqliteRegistry(tmp_path / "r.db")
    store = make_store()
    bm = Bm25Store()
    svc = IngestionService(reg, store.inner, EMB, bm25=bm)
    p = Path(__file__).parent / "fixtures" / "sample.txt"
    doc = await svc.ingest("u1", str(p), "sample.txt", "text/plain")
    assert doc.status == "ready" and bm.has_doc("u1", doc.doc_id)
    assert await svc.delete("u1", doc.doc_id)
    assert not bm.has_doc("u1", doc.doc_id)


async def test_ingested_corpus_rebuilds_bm25_after_restart(tmp_path):
    from pathlib import Path

    reg = SqliteRegistry(tmp_path / "r.db")
    store = make_store()
    svc = IngestionService(reg, store.inner, EMB, bm25=Bm25Store())
    p = Path(__file__).parent / "fixtures" / "sample.txt"
    doc = await svc.ingest("u1", str(p), "sample.txt", "text/plain")
    assert doc.status == "ready"
    fresh = Bm25Store()  # simulated restart: only the VectorStore survives
    hits = hybrid_search("u1", "sample", EMB.encode(["sample"])[0], store, fresh, top_k=3)
    assert hits and fresh.has_doc("u1", doc.doc_id)

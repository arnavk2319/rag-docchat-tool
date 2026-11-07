"""T-011: rerank, score abstention, citations, eval line (fake models, mock LLM)."""

import pytest
from app.core import config, llm
from app.core.models import FakeReranker
from app.main import app
from app.services.parent_lookup import ParentHit
from app.services.rerank import rerank_parents
from app.store.parents import Parent
from fastapi.testclient import TestClient

A = {"X-User-Id": "alice"}

DOCS = {
    "invoice.txt": b"The invoice number is INV-4471 and payment is due in thirty days.",
    "hotel.txt": b"Residence Inn Toronto Vaughan reservation confirmation number 84176338.",
    "travel.txt": b"Pack the passport, charger, and adapter before the flight to Lisbon.",
    "revenue.txt": b"Quarterly revenue grew twelve percent driven by subscription renewals.",
    "bio.txt": b"Photosynthesis converts sunlight into glucose inside chloroplasts.",
}
IN_CORPUS = [
    "What is the invoice number and when is payment due?",
    "Which reservation confirmation number at Residence Inn Toronto?",
    "What to pack before the flight to Lisbon, passport and adapter?",
    "How much did quarterly revenue grow from subscription renewals?",
    "Where does photosynthesis convert sunlight into glucose?",
]
OUT_OF_CORPUS = [
    "Who won the football championship yesterday?",
    "Explain quantum entanglement experiments",
    "Recipe for sourdough bread starter",
    "Capital city of Mongolia population",
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MIN_RERANK_SCORE", "0.3")
    config.get_settings.cache_clear()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _up(c, files):
    r = c.post("/v1/documents", files=[("files", f) for f in files.items()], headers=A)
    assert r.status_code == 200, r.text
    return {x["filename"]: x["doc_id"] for x in r.json()["results"]}


def _count_llm(monkeypatch):
    calls = []
    real = llm.complete

    async def spy(*a, **k):
        calls.append(1)
        return await real(*a, **k)

    monkeypatch.setattr(llm, "complete", spy)
    return calls


def test_abstains_below_threshold_with_zero_llm_calls(client, monkeypatch):
    _up(client, DOCS)
    calls = _count_llm(monkeypatch)
    b = client.post("/v1/query", json={"question": OUT_OF_CORPUS[0]}, headers=A).json()
    assert b["abstained"] is True and b["citations"] == [] and "know" in b["answer"]
    assert calls == []


def test_answers_above_threshold_with_valid_citations(client, monkeypatch):
    ids = _up(client, DOCS)
    calls = _count_llm(monkeypatch)
    b = client.post("/v1/query", json={"question": IN_CORPUS[0]}, headers=A).json()
    assert b["abstained"] is False and len(calls) == 1 and b["citations"]
    c = b["citations"][0]
    assert c["doc_id"] == ids["invoice.txt"] and c["filename"] == "invoice.txt"
    assert c["chunk_id"] and c["page"] is None or isinstance(c["page"], int)
    src = DOCS["invoice.txt"].decode()
    sp = c["span"]
    assert "INV-4471" in src[sp["char_start"] : sp["char_end"]]


def test_eval_citation_correct_and_abstain_rate(client):
    ids = _up(client, DOCS)
    want = ["invoice.txt", "hotel.txt", "travel.txt", "revenue.txt", "bio.txt"]
    ok = 0
    for q, f in zip(IN_CORPUS, want, strict=True):
        b = client.post("/v1/query", json={"question": q}, headers=A).json()
        cs = b["citations"]
        if not b["abstained"] and cs and cs[0]["doc_id"] == ids[f]:
            sp = cs[0]["span"]
            ok += DOCS[f].decode()[sp["char_start"] : sp["char_end"]] != ""
    abst = sum(
        client.post("/v1/query", json={"question": q}, headers=A).json()["abstained"]
        for q in OUT_OF_CORPUS
    )
    print(f"citation_correct={ok / 5:.2f} abstain_rate={abst / 4:.2f}")
    assert ok / 5 >= 0.9
    assert abst == len(OUT_OF_CORPUS)


def test_bm25_placeholder_distance_does_not_cause_abstention():
    def ph(pid, text, dist):
        p = Parent(owner_id="u", doc_id="d", parent_id=pid, char_start=0, char_end=len(text), text=text)
        return ParentHit(parent=p, best_child_id=pid + "c", distance=dist, score=1 - dist)

    hits = [ph("a", "unrelated words here", 0.1), ph("b", "invoice number INV-4471 due", 1.0)]
    out = rerank_parents(FakeReranker(), "invoice number due", hits, top_k=5)
    assert out[0][0].parent.parent_id == "b" and out[0][1] > 0.5


def test_rerank_top_k_ties_and_empty():
    def ph(pid):
        p = Parent(owner_id="u", doc_id="d", parent_id=pid, char_start=0, char_end=1, text="zzz")
        return ParentHit(parent=p, best_child_id=pid, distance=0.5, score=0.5)

    assert rerank_parents(FakeReranker(), "q", [], 5) == []
    out = rerank_parents(FakeReranker(), "q", [ph("a"), ph("b"), ph("c")], top_k=2)
    assert [h.parent.parent_id for h, _ in out] == ["a", "b"]


def test_query_works_without_any_compression_step(client):
    """Legacy compressor=None crash is gone: rerank path has no optional compressor."""
    _up(client, DOCS)
    r = client.post("/v1/query", json={"question": IN_CORPUS[1], "top_k": 1}, headers=A)
    assert r.status_code == 200 and len(r.json()["citations"]) == 1


def test_citation_picks_child_containing_evidence(client):
    filler = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 120
    text = (filler + "The invoice number is INV-4471 for this order. " + filler).encode()
    assert len(text) > 10_000
    _up(client, {"big.txt": text})
    b = client.post(
        "/v1/query", json={"question": "What is the invoice number INV-4471?"}, headers=A
    ).json()
    assert b["abstained"] is False and b["citations"]
    sp = b["citations"][0]["span"]
    cited = text.decode()[sp["char_start"] : sp["char_end"]]
    assert "INV-4471" in cited

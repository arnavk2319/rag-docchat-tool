import pytest
from app.core import llm
from app.services import summarization
from app.services.agent.tools import MAX_COMPARE_DOCS, ToolContext, run_tool
from app.store.chroma import Hit
from app.store.parents import Parent, SqliteParents
from app.store.registry import SqliteRegistry


class FakeVec:
    def __init__(self, hits):
        self.hits = hits

    def query(self, owner_id, embedding, k=5, doc_id=None, doc_ids=None):
        return [h for h in self.hits if h.metadata["owner_id"] == owner_id
                and (doc_ids is None or h.metadata["doc_id"] in doc_ids)]

    def get(self, owner_id, ids):
        return []


class FakeReranker:
    def predict(self, pairs):
        return [1.0] * len(pairs)


@pytest.fixture
def env(tmp_path):
    summarization.clear_cache()
    reg = SqliteRegistry(tmp_path / "r.db")
    par = SqliteParents(tmp_path / "r.db")
    ids, hits = {}, []
    for owner in ("alice", "bob"):
        for n in (1, 2):
            d = reg.create(owner, f"{owner}{n}.pdf", "application/pdf", 1, f"sha{owner}{n}", 1, "ready")
            ids[(owner, n)] = d
            text = f"{owner} doc {n} <b>content</b> about pricing"
            par.replace_doc(owner, d, [Parent(owner_id=owner, doc_id=d, parent_id=f"p{d}", page=1,
                                              section="S", char_start=0, char_end=5, text=text)])
            hits.append(Hit(chunk_id=f"c{d}", text=text, distance=0.1,
                            metadata={"owner_id": owner, "doc_id": d, "parent_id": f"p{d}"}))
    ctx = ToolContext("alice", None, reg, FakeVec(hits), par, None, lambda q: [0.0], FakeReranker())
    return ctx, ids


@pytest.fixture
def calls(monkeypatch):
    seen = []
    real = llm.complete

    async def spy(messages, **kw):
        seen.append(messages[0]["content"])
        return await real(messages, **kw)

    monkeypatch.setattr(llm, "complete", spy)
    return seen


def test_summarize_and_cache(env, calls):
    ctx, ids = env
    d = ids[("alice", 1)]
    out = run_tool("summarize_document", {"doc_id": d}, ctx)
    assert 'status="ok"' in out and f"doc_id={d}" in out and "mock-reply" in out
    n = len(calls)
    assert n >= 1
    again = run_tool("summarize_document", {"doc_id": d}, ctx)
    assert len(calls) == n and "mock-reply" in again


def test_summarize_scope(env, calls):
    ctx, ids = env
    foreign = run_tool("summarize_document", {"doc_id": ids[("bob", 1)]}, ctx)
    missing = run_tool("summarize_document", {"doc_id": "nope"}, ctx)
    assert foreign == missing and "not found" in foreign and not calls
    ctx.doc_ids = [ids[("alice", 1)]]
    assert "not found" in run_tool("summarize_document", {"doc_id": ids[("alice", 2)]}, ctx)
    assert "invalid arguments" in run_tool("summarize_document", {"doc_id": "x", "z": 1}, ctx)
    ctx.doc_ids = None
    ctx.registry.update_status("alice", ids[("alice", 2)], "failed")
    assert run_tool("summarize_document", {"doc_id": ids[("alice", 2)]}, ctx) == missing


def test_compare_cites_both(env, calls):
    ctx, ids = env
    a, b = ids[("alice", 1)], ids[("alice", 2)]
    out = run_tool("compare_documents", {"doc_ids": [a, b], "focus": "pricing"}, ctx)
    assert 'status="ok"' in out and f"doc_id={a}" in out and f"doc_id={b}" in out
    assert len(calls) == 1 and "alice doc 1" in calls[0] and "alice doc 2" in calls[0]
    assert "<b>" not in calls[0]


def test_compare_limits_and_scope(env, calls):
    ctx, ids = env
    a = ids[("alice", 1)]
    many = [a] + [f"d{i}" for i in range(MAX_COMPARE_DOCS)]
    assert "invalid arguments" in run_tool("compare_documents", {"doc_ids": many}, ctx)
    assert "invalid arguments" in run_tool("compare_documents", {"doc_ids": [a]}, ctx)
    foreign = run_tool("compare_documents", {"doc_ids": [a, ids[("bob", 1)]]}, ctx)
    missing = run_tool("compare_documents", {"doc_ids": [a, "nope"]}, ctx)
    assert foreign == missing and "not found" in foreign
    assert "not found" in run_tool("compare_documents", {"doc_ids": [a, a]}, ctx)
    assert not calls

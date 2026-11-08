"""T-037: chunk-level citations in the agent SSE stream (real app, fake models, mock LLM)."""

import json

import pytest
from app.api import sse
from app.api.schemas import Citation
from app.core import llm
from app.main import app
from app.services.agent.graph import AgentResult
from app.services.agent.tools import ToolContext
from authutil import bearer
from fastapi.testclient import TestClient

A = bearer("alice")
B = bearer("bob")


def J(**kw):
    return json.dumps(kw)


ROUTE = J(route="retrieve", answer="")
SEARCH = J(action="tool", tool="search_documents", args={"query": "sky colour"})
FINAL = J(action="final", tool="", args={})
YES, GOOD = J(relevant=True), J(grounded=True)


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def upload(c, headers=A, name="a.txt", text=b"The sky colour is blue. Grass is green."):
    r = c.post("/v1/documents", files=[("files", (name, text))], headers=headers)
    return r.json()["results"][0]["doc_id"]


def events(text: str):
    out = []
    for block in text.split("\n\n"):
        if block.strip() and not block.startswith(":"):
            lines = block.split("\n")
            out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


def run(c, d, headers=A, q="sky colour?"):
    llm.mock_script(ROUTE, SEARCH, FINAL, YES, f"Blue [doc_id={d}]", GOOD)
    r = c.post("/v1/query", json={"question": q, "stream": True}, headers=headers)
    assert r.status_code == 200
    return events(r.text)


def test_stream_citations_are_chunk_level_and_match_model(client):
    d = upload(client)
    ev = run(client, d)
    cites = ev[-1][1]["citations"]
    assert cites and ev[-2][0] == "citations" and ev[-2][1]["citations"] == cites
    for raw in cites:
        c = Citation.model_validate(raw)  # shared schema (non-stream path)
        assert c.doc_id == d and c.filename == "a.txt" and c.chunk_id
        assert 0 <= c.span.char_start <= c.span.char_end
        assert set(raw) == {"doc_id", "filename", "chunk_id", "page", "sheet", "span"}
        assert set(raw["span"]) == {"char_start", "char_end"}


def test_no_raw_chunk_text_in_citations(client):
    d = upload(client)
    cites = run(client, d)[-1][1]["citations"]
    assert "Grass is green" not in json.dumps(cites)


def test_filename_escaped(client):
    d = upload(client, name="x<b>.txt")
    cites = run(client, d)[-1][1]["citations"]
    assert cites and "<b>" not in cites[0]["filename"]


def test_other_tenant_gets_no_foreign_chunks(client):
    d = upload(client)
    b = upload(client, headers=B, text=b"Bob sky colour grey.", name="b.txt")
    cites = run(client, b, headers=B)[-1][1]["citations"]
    assert cites and all(c["doc_id"] == b for c in cites) and d not in json.dumps(cites)


class Vec:
    def get(self, owner, ids):
        return []


class Par:
    def get_parents(self, owner, ids):
        return []

    def list_doc_parents(self, owner, doc):
        return []


def test_fabricated_chunk_id_dropped():
    ctx = ToolContext("alice", None, None, Vec(), Par(), None, lambda q: [0.0], None)
    res = AgentResult(answer="a", citations=[{"doc_id": "d1", "span": "x"}],
                      cited_chunks={"fabricated": "d1"})
    assert sse.build_citations(ctx, res, {"d1": "a.txt"}) == []


def test_abstain_run_empty_citations(client):
    d = upload(client)
    llm.mock_script(ROUTE, SEARCH, FINAL, J(relevant=False), J(rewrite="x"), SEARCH, FINAL,
                    J(relevant=False))
    r = client.post("/v1/query", json={"question": "moon?", "stream": True}, headers=A)
    ev = events(r.text)
    fin = ev[-1][1]
    assert fin["abstained"] and fin["citations"] == [] and d not in json.dumps(fin["citations"])


def _real_stores(tmp_path):
    from app.store.parents import Parent, SqliteParents
    from app.vectorstore.memory import InMemoryVectorStore
    from app.vectorstore.sync import SyncVectors

    vec, par = SyncVectors(InMemoryVectorStore()), SqliteParents(tmp_path / "p.db")
    par.replace_doc("alice", "da", [Parent(owner_id="alice", doc_id="da", parent_id="da:p0",
                                           char_start=0, char_end=40, text="alice secret")])
    vec.add(["da:p0:c0"], [[1.0] + [0.0] * 383], ["alice secret"],
            [{"owner_id": "alice", "doc_id": "da", "parent_id": "da:p0", "char_start": 0, "char_end": 10}])
    return vec, par


def test_build_citations_real_stores_scope_by_owner_and_names(tmp_path):
    vec, par = _real_stores(tmp_path)
    res = AgentResult(answer="a", citations=[{"doc_id": "da", "span": ""}],
                      cited_chunks={"da:p0:c0": "da", "da:p0": "da"})

    def mk(owner):
        return ToolContext(owner, None, None, vec, par, None, lambda q: [0.0], None)

    got = sse.build_citations(mk("alice"), res, {"da": "a.txt"})
    assert {c.chunk_id for c in got} >= {"da:p0:c0"} and all(c.doc_id == "da" for c in got)
    assert sse.build_citations(mk("bob"), res, {"da": "a.txt"}) == []  # Bob cannot re-read Alice's ids
    assert sse.build_citations(mk("alice"), res, {"other": "o.txt"}) == []  # doc outside names

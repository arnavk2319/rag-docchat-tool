"""T-029: tenant isolation across registry, vectors, sessions, parents, BM25 and credentials."""

import asyncio
from pathlib import Path

import pytest
from app.core.auth import InMemoryKeyStore, issue_api_key, mint_token
from app.main import app
from app.store.registry import Document
from app.store.sessions import SessionMessage
from app.vectorstore import ChunkRecord, InMemoryVectorStore
from app.vectorstore.base import ScoredChunk
from authutil import TEST_SECRET
from fastapi.testclient import TestClient


def hdr(tenant: str, owner: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(owner, tenant, TEST_SECRET)}"}


A_ALICE, A_BOB, B_ALICE = hdr("ta", "alice"), hdr("ta", "bob"), hdr("tb", "alice")


@pytest.fixture
def c():
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def upload(c, h=A_ALICE, body=b"The sky is blue."):
    r = c.post("/v1/documents", files=[("files", ("a.txt", body))], headers=h)
    return r.json()["results"][0]["doc_id"]


def test_documents_cross_tenant_and_cross_owner_are_404(c):
    did = upload(c)
    assert c.get(f"/v1/documents/{did}", headers=A_ALICE).status_code == 200
    for other in (B_ALICE, A_BOB):  # other tenant (same owner id), same tenant other owner
        assert c.get("/v1/documents", headers=other).json()["items"] == []
        assert c.get(f"/v1/documents/{did}", headers=other).status_code == 404
        assert c.delete(f"/v1/documents/{did}", headers=other).status_code == 404
        r = c.post("/v1/query", json={"question": "sky?", "doc_ids": [did]}, headers=other)
        assert r.status_code == 404
        assert c.post("/v1/summarize", json={"doc_id": did}, headers=other).status_code == 404
    assert c.get(f"/v1/documents/{did}", headers=A_ALICE).status_code == 200  # untouched


def test_same_owner_id_same_content_in_two_tenants_is_two_documents(c):
    a, b = upload(c, A_ALICE), upload(c, B_ALICE)
    assert a != b
    assert c.delete(f"/v1/documents/{a}", headers=A_ALICE).status_code == 204
    assert c.get(f"/v1/documents/{b}", headers=B_ALICE).status_code == 200


def test_query_without_doc_ids_never_cites_other_tenants_chunks(c):
    upload(c, A_ALICE, b"The zebra crossing is striped.")
    r = c.post("/v1/query", json={"question": "zebra striped?"}, headers=B_ALICE)
    assert r.status_code == 200 and r.json()["citations"] == []


def test_sessions_cross_tenant_and_cross_owner_are_404(c):
    sid = c.post("/v1/sessions", headers=A_ALICE).json()["session_id"]
    for other in (B_ALICE, A_BOB):
        assert c.get(f"/v1/sessions/{sid}", headers=other).status_code == 404
        assert c.delete(f"/v1/sessions/{sid}", headers=other).status_code == 404
        r = c.post("/v1/query", json={"question": "q", "session_id": sid}, headers=other)
        assert r.status_code == 404
    assert c.get(f"/v1/sessions/{sid}", headers=A_ALICE).status_code == 200


def test_spoofed_tenant_in_headers_query_and_body_is_ignored(c):
    did = upload(c, A_ALICE)
    spoof = {**B_ALICE, "X-Tenant-Id": "ta", "X-Tenant": "ta"}
    assert c.get(f"/v1/documents/{did}?tenant_id=ta", headers=spoof).status_code == 404
    r = c.post("/v1/query", json={"question": "q", "doc_ids": [did], "tenant_id": "ta"}, headers=spoof)
    assert r.status_code in (404, 422)
    assert c.get("/v1/documents", headers=spoof).json()["items"] == []


def test_api_key_owner_ids_are_bound_to_key_tenant(c):
    ks = InMemoryKeyStore()
    app.state.key_store = ks
    try:
        did = upload(c, B_ALICE)  # tenant tb, owner alice
        key_a = issue_api_key(ks, "ta", TEST_SECRET)
        key_b = issue_api_key(ks, "tb", TEST_SECRET)
        as_a = {"Authorization": f"Bearer {key_a}", "X-On-Behalf-Of": "alice"}
        as_b = {"Authorization": f"Bearer {key_b}", "X-On-Behalf-Of": "alice"}
        # tenant A's key naming tenant B's owner id sees nothing of tenant B
        assert c.get(f"/v1/documents/{did}", headers=as_a).status_code == 404
        assert c.delete(f"/v1/documents/{did}", headers=as_a).status_code == 404
        assert c.get("/v1/documents", headers=as_a).json()["items"] == []
        assert c.get(f"/v1/documents/{did}", headers=as_b).status_code == 200
    finally:
        app.state.key_store = None


def test_vector_queries_always_filtered_by_tenant():
    store = InMemoryVectorStore()
    vec = [1.0] + [0.0] * 383

    async def go():
        for t in ("ta", "tb"):
            await store.upsert_chunks(t, "alice", "d1", [ChunkRecord(chunk_id=f"{t}:c", text=t)], [vec])
        hits = await store.query(vec, 10, "ta", "alice")
        assert [h.chunk_id for h in hits] == ["ta:c"] and hits[0].tenant_id == "ta"
        assert await store.get_chunks("ta", "alice", ["tb:c"]) == []
        assert [h.chunk_id for h in await store.list_chunks("tb", "alice")] == ["tb:c"]
        assert await store.delete_doc("ta", "alice", "d1") == 1
        assert await store.count("tb", "alice") == 1
        assert await store.query(vec, 10, "tc", "alice") == []

    asyncio.run(go())


def test_sync_view_is_bound_to_tenant():
    from app.vectorstore.sync import SyncVectors

    root = SyncVectors(InMemoryVectorStore())
    a, b = root.for_tenant("ta"), root.for_tenant("tb")
    a.add(["c1"], [[1.0] + [0.0] * 383], ["x"], [{"owner_id": "o", "doc_id": "d"}])
    assert [h.chunk_id for h in a.query("o", [1.0] + [0.0] * 383)] == ["c1"]
    assert b.query("o", [1.0] + [0.0] * 383) == [] and b.list_chunks("o") == []


def test_registry_and_session_views_isolate_tenants():
    from app.store.registry import InMemoryRegistry
    from app.store.sessions import InMemorySessions

    reg = InMemoryRegistry()
    ra, rb = reg.for_tenant("ta"), reg.for_tenant("tb")
    d = ra.create("o", "f.txt", "text/plain", 1, "sha")
    assert ra.get("o", d).tenant_id == "ta" and rb.get("o", d) is None
    assert rb.list_by_owner("o") == [] and not rb.delete("o", d) and not rb.update_status("o", d, "ready")
    assert rb.create("o", "f.txt", "text/plain", 1, "sha") != d  # dedupe is per tenant
    ss = InMemorySessions()
    sa, sb = ss.for_tenant("ta"), ss.for_tenant("tb")
    sid = sa.create("o", max_per_owner=1)
    assert sb.messages("o", sid) is None and not sb.exists("o", sid) and not sb.delete("o", sid)
    assert not sb.append_turn("o", sid, "q", "a", [])
    sb.create("o", max_per_owner=1)  # the cap counts per tenant
    assert sa.append_turn("o", sid, "q", "a", []) and sa.messages("o", sid)[0].tenant_id == "ta"


def test_bm25_and_parents_views_do_not_share_owner_keys(tmp_path):
    from app.store.bm25 import Bm25Store
    from app.store.parents import Parent, SqliteParents

    bm, pr = Bm25Store(), SqliteParents(tmp_path / "p.db")
    ba, bb = bm.for_tenant("ta"), bm.for_tenant("tb")
    ba.add_doc("o", "d1", ["c1"], ["zebra stripes"])
    assert ba.query("o", "zebra") and bb.query("o", "zebra") == [] and not bb.has_doc("o", "d1")
    p = Parent(owner_id="o", doc_id="d1", parent_id="p1", char_start=0, char_end=3, text="abc")
    pr.for_tenant("ta").replace_doc("o", "d1", [p])
    assert pr.for_tenant("ta").list_doc_parents("o", "d1")[0].owner_id == "o"
    assert pr.for_tenant("tb").list_doc_parents("o", "d1") == []
    assert pr.for_tenant("tb").get_parents("o", ["p1"]) == []


def test_tenant_id_field_on_each_model():
    for model in (Document, ScoredChunk, SessionMessage):
        assert "tenant_id" in model.model_fields, model
    sql = Path("migrations/0001_init.sql").read_text()
    for table in ("vector_chunks", "documents", "sessions", "session_messages"):
        body = sql.split(f"CREATE TABLE IF NOT EXISTS {table} (")[1].split(");")[0]
        assert "tenant_id text NOT NULL" in body, table

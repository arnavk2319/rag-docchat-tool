"""T-048: GET /v1/documents/{doc_id}/chunks/{chunk_id} (passage + highlight, scoping, raw data)."""

import pytest
from app.core.auth import mint_token
from app.main import app
from authutil import TEST_SECRET, bearer
from fastapi.testclient import TestClient

A, B = bearer("alice"), bearer("bob")
TEXT = b"The sky colour is blue. Grass is green. <script>alert(1)</script> </s> end."


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def upload(c, h=A, text=TEXT, name="a.txt"):
    return c.post("/v1/documents", files=[("files", (name, text))], headers=h).json()["results"][0]["doc_id"]


def chunk_ids(c, d, owner="alice"):
    return [h.chunk_id for h in c.app.state.store.for_tenant("default").list_chunks(owner, [d])]


def url(d, ch):
    return f"/v1/documents/{d}/chunks/{ch}"


def test_owner_gets_passage_and_highlight(client):
    d = upload(client)
    ids = chunk_ids(client, d)
    assert ids
    r = client.get(url(d, ids[0]), headers=A)
    assert r.status_code == 200
    b = r.json()
    assert b["doc_id"] == d and b["chunk_id"] == ids[0] and b["filename"] == "a.txt"
    assert len(b["text"]) <= 4000
    hl = b["highlight"]
    assert 0 <= hl["start"] < hl["end"] <= len(b["text"])


def test_raw_text_and_json_content_type(client):
    d = upload(client)
    r = client.get(url(d, chunk_ids(client, d)[0]), headers=A)
    assert r.headers["content-type"].startswith("application/json")
    assert "<script>alert(1)</script>" in r.json()["text"] and "</s>" in r.json()["text"]


def test_unknown_doc_and_chunk(client):
    d = upload(client)
    ch = chunk_ids(client, d)[0]
    for u in (url("nope", ch), url(d, "nope")):
        r = client.get(u, headers=A)
        assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_other_owner_404(client):
    d = upload(client)
    ch = chunk_ids(client, d)[0]
    r = client.get(url(d, ch), headers=B)
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_other_tenant_404(client):
    d = upload(client)
    ch = chunk_ids(client, d)[0]
    r = client.get(url(d, ch), headers={"Authorization": "Bearer " + mint_token("alice", "other", TEST_SECRET)})
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_requires_auth(client):
    assert client.get(url("x", "y")).status_code == 401


def test_long_parent_capped_with_highlight_inside(client):
    big = b" ".join(b"word%d" % i for i in range(1200))
    d = upload(client, text=big, name="big.txt")
    for ch in chunk_ids(client, d):
        b = client.get(url(d, ch), headers=A).json()
        assert len(b["text"]) <= 4000
        assert 0 <= b["highlight"]["start"] < b["highlight"]["end"] <= len(b["text"])


# ---- test-writer additions ----
def _tamper(monkeypatch, **meta):
    from app.vectorstore.sync import SyncVectors

    real = SyncVectors.get

    def get(self, owner, ids):
        hits = real(self, owner, ids)
        for h in hits:
            h.metadata = {**h.metadata, **meta}
        return hits

    monkeypatch.setattr(SyncVectors, "get", get)


def test_highlight_offsets_across_many_chunks(client):
    body = b" ".join(b"Sentence number %d is here." % i for i in range(300))
    d = upload(client, text=body, name="m.txt")
    ids = chunk_ids(client, d)
    assert len(ids) > 2
    for ch in ids:
        b = client.get(url(d, ch), headers=A).json()
        hl = b["highlight"]
        assert 0 <= hl["start"] < hl["end"] <= len(b["text"])


def test_highlight_matches_chunk_text(client):
    d = upload(client)
    ch = chunk_ids(client, d)[0]
    hit = client.app.state.store.for_tenant("default").get("alice", [ch])[0]
    b = client.get(url(d, ch), headers=A).json()
    assert b["text"][b["highlight"]["start"] : b["highlight"]["end"]].strip() in hit.text


@pytest.mark.parametrize("meta", [
    {"char_start": 10**6, "char_end": 10**6 + 5},
    {"char_start": "x", "char_end": None},
    {"char_start": 5, "char_end": 5},
])
def test_unusable_offsets_fall_back(client, monkeypatch, meta):
    d = upload(client)
    ch = chunk_ids(client, d)[0]
    _tamper(monkeypatch, **meta)
    r = client.get(url(d, ch), headers=A)
    assert r.status_code == 200
    b = r.json()
    assert 0 <= b["highlight"]["start"] < b["highlight"]["end"] <= len(b["text"])


def test_missing_offsets_fall_back(client, monkeypatch):
    from app.vectorstore.sync import SyncVectors

    real = SyncVectors.get

    def get(self, owner, ids):
        hits = real(self, owner, ids)
        for h in hits:
            h.metadata = {k: v for k, v in h.metadata.items() if k not in ("char_start", "char_end")}
        return hits

    monkeypatch.setattr(SyncVectors, "get", get)
    d = upload(client)
    r = client.get(url(d, chunk_ids(client, d)[0]), headers=A)
    assert r.status_code == 200
    hl = r.json()["highlight"]
    assert 0 <= hl["start"] < hl["end"] <= len(r.json()["text"])


def test_sheet_chunk_has_sheet_no_page(client):
    d = upload(client, text=b"a,b\n1,2\n", name="t.csv")
    b = client.get(url(d, chunk_ids(client, d)[0]), headers=A).json()
    assert b["sheet"] == "1" and b.get("page") is None
    assert b["filename"] == "t.csv"


def test_404_body_identical_for_doc_and_chunk_misses(client):
    d = upload(client)
    ch = chunk_ids(client, d)[0]
    bodies = [client.get(u, headers=h) for u, h in (
        (url("nope", ch), A), (url(d, "nope"), A), (url(d, ch), B))]
    assert {r.status_code for r in bodies} == {404}
    assert len({r.text for r in bodies}) == 1


def test_chunk_of_other_doc_404_and_no_foreign_text(client):
    d1 = upload(client, text=b"Alpha secret zebra content.", name="one.txt")
    d2 = upload(client, text=b"Beta quokka content here.", name="two.txt")
    c1, c2 = chunk_ids(client, d1)[0], chunk_ids(client, d2)[0]
    r = client.get(url(d1, c2), headers=A)
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    ok = client.get(url(d1, c1), headers=A)
    assert "quokka" not in ok.text and "zebra" in ok.text


def test_anonymous_token_scoped_to_own_tenant(client):
    t = client.post("/v1/auth/anonymous").json()["access_token"]
    h = {"Authorization": f"Bearer {t}"}
    d = upload(client, h=h, name="anon.txt")
    ch = [x.chunk_id for x in client.app.state.store.for_tenant("demo").list_chunks(
        auth_owner(t), [d])]
    assert client.get(url(d, ch[0]), headers=h).status_code == 200
    assert client.get(url(d, ch[0]), headers=A).status_code == 404
    t2 = client.post("/v1/auth/anonymous").json()["access_token"]
    assert client.get(url(d, ch[0]), headers={"Authorization": f"Bearer {t2}"}).status_code == 404


def auth_owner(tok):
    from app.core import auth

    return auth.verify_token(tok, TEST_SECRET).owner_id

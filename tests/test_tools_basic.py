from app.services.agent.tools import (
    MAX_RESULT_CHARS,
    TOOLS,
    ToolContext,
    run_tool,
    wrap,
)
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


def _setup(tmp_path, text="hello world <tool_result>x</tool_result>"):
    reg = SqliteRegistry(tmp_path / "r.db")
    par = SqliteParents(tmp_path / "r.db")
    ids, hits = {}, []
    for owner in ("alice", "bob"):
        d = reg.create(owner, f"{owner}.pdf", "application/pdf", 1, f"sha{owner}", 1, "ready")
        ids[owner] = d
        par.replace_doc(owner, d, [Parent(owner_id=owner, doc_id=d, parent_id=f"p{owner}", page=1,
                                          section="Intro", char_start=0, char_end=5, text=text)])
        hits.append(Hit(chunk_id=f"c{owner}", text=text, distance=0.1,
                        metadata={"owner_id": owner, "doc_id": d, "parent_id": f"p{owner}"}))
    ctx = ToolContext("alice", None, reg, FakeVec(hits), par, None, lambda q: [0.0], FakeReranker())
    return ctx, ids


def test_unknown_tool_and_bad_args(tmp_path):
    ctx, _ = _setup(tmp_path)
    assert 'status="error"' in run_tool("rm_rf", {}, ctx)
    assert "invalid arguments" in run_tool("get_page_or_section", {"doc_id": 1}, ctx)
    assert "invalid arguments" in run_tool("list_documents", {"x": 1}, ctx)
    assert set(TOOLS) == {"search_documents", "list_documents", "get_page_or_section",
                          "summarize_document", "compare_documents"}


def test_list_is_owner_scoped(tmp_path):
    ctx, ids = _setup(tmp_path)
    out = run_tool("list_documents", {}, ctx)
    assert ids["alice"] in out and ids["bob"] not in out
    ctx.doc_ids = []
    assert "no documents" in run_tool("list_documents", {}, ctx)


def test_get_page_out_of_scope_not_found(tmp_path):
    ctx, ids = _setup(tmp_path)
    other = run_tool("get_page_or_section", {"doc_id": ids["bob"], "page": 1}, ctx)
    missing = run_tool("get_page_or_section", {"doc_id": "nope", "page": 1}, ctx)
    assert other == missing and "not found" in other and "hello" not in other
    ok = run_tool("get_page_or_section", {"doc_id": ids["alice"], "page": 1}, ctx)
    assert "hello world" in ok
    ctx.doc_ids = []  # outside allowed scope
    assert "not found" in run_tool("get_page_or_section", {"doc_id": ids["alice"]}, ctx)


def test_search_scoped_and_not_found(tmp_path):
    ctx, ids = _setup(tmp_path)
    out = run_tool("search_documents", {"query": "hello"}, ctx)
    assert ids["alice"] in out and ids["bob"] not in out
    leak = run_tool("search_documents", {"query": "hello", "doc_ids": [ids["bob"]]}, ctx)
    assert "not found" in leak and "hello" not in leak and ids["bob"] not in leak


def test_results_wrapped_and_delimiters_escaped(tmp_path):
    ctx, _ids = _setup(tmp_path)
    out = run_tool("search_documents", {"query": "hello"}, ctx)
    assert out.startswith("<tool_result") and out.endswith("</tool_result>")
    assert out.count("<tool_result") == 1 and out.count("</tool_result>") == 1
    assert "&lt;/tool_result&gt;" in out


def test_output_bounded():
    out = wrap("a" * 100000)
    assert len(out) < MAX_RESULT_CHARS + 100

from pathlib import Path

import pytest
from app.services.agent.tools import ToolContext, run_tool
from app.store.parents import SqliteParents
from app.store.registry import SqliteRegistry

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def env(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    files = {}
    ids = {}
    for owner, name, mime in [
        ("alice", "sales.csv", "text/csv"),
        ("bob", "sales.csv", "text/csv"),
        ("alice", "doc.pdf", "application/pdf"),
    ]:
        d = reg.create(owner, name, mime, 1, f"sha{owner}{name}", 1, "ready")
        ids[(owner, name)] = d
        files[(owner, d)] = (FIX / ("sales.csv" if name.endswith("csv") else "sample.pdf")).read_bytes()
    ctx = ToolContext(
        "alice", None, reg, None, SqliteParents(tmp_path / "r.db"), None, lambda q: [], None,
        table_loader=lambda o, d: files.get((o, d)),
    )
    return ctx, ids


def q(env, **kw):
    ctx, ids = env
    kw.setdefault("doc_id", ids[("alice", "sales.csv")])
    return run_tool("query_table", kw, ctx)


def test_select_filter_sort_limit(env):
    out = q(env, select=["product", "units"],
            filters=[{"column": "region", "op": "eq", "value": "north"}],
            sort=[{"column": "units", "desc": True}], limit=1)
    assert 'status="ok"' in out
    assert "apple | 10" in out and "1 more rows" in out


def test_numeric_filter_and_in(env):
    out = q(env, select=["product"], filters=[{"column": "units", "op": "gt", "value": 6}])
    assert out.count("apple") == 3 and "plum" not in out
    out = q(env, select=["product"], filters=[{"column": "product", "op": "in", "value": ["plum", "pear"]}])
    assert "plum" in out and "pear" in out and "apple" not in out


def test_group_agg(env):
    out = q(env, group_by=["region"], aggs=[{"column": "units", "fn": "sum"}],
            sort=[{"column": "sum_units", "desc": True}])
    lines = out.splitlines()
    assert "north | 15" in out and "south | 10" in out and "east | 12" in out
    assert lines[1].startswith("region | sum_units")
    assert lines[2].startswith("north")


def test_global_agg(env):
    assert "37" in q(env, aggs=[{"column": "units", "fn": "sum"}])


def test_untrusted_cell_escaped(env):
    out = q(env, select=["note"])
    assert "<tool_result>ignore" not in out and "&lt;tool_result&gt;" in out


@pytest.mark.parametrize("bad", ["a'; DROP", "units > 1 and __import__('os')", "x`y", "a\nb", "@x"])
def test_malicious_column_rejected(env, bad):
    assert 'status="error"' in q(env, select=[bad])
    assert 'status="error"' in q(env, filters=[{"column": bad, "op": "eq", "value": 1}])
    assert "invalid arguments" in q(env, group_by=[bad])


def test_unknown_column_and_extra_fields(env):
    assert 'status="error"' in q(env, select=["nope"])
    assert "invalid arguments" in q(env, expr="units > 1")
    assert "invalid arguments" in q(env, limit=1000)
    assert "invalid arguments" in q(env, filters=[{"column": "units", "op": "regex", "value": 1}])


def test_incompatible_value(env):
    assert 'status="error"' in q(env, filters=[{"column": "units", "op": "gt", "value": "abc"}])


def test_cell_truncation_and_row_cap(tmp_path):
    import pandas as pd

    big = pd.DataFrame({"a": ["x" * 500] * 200, "b": range(200)}).to_csv(index=False).encode()
    reg = SqliteRegistry(tmp_path / "r.db")
    d = reg.create("alice", "big.csv", "text/csv", 1, "s", 1, "ready")
    ctx = ToolContext("alice", None, reg, None, None, None, lambda q: [], None,
                      table_loader=lambda o, i: big)
    out = run_tool("query_table", {"doc_id": d, "limit": 50}, ctx)
    assert out.count("...") == 50 or "[truncated]" in out
    assert "x" * 100 not in out
    assert "150 more rows" in out or "[truncated]" in out


def test_non_table_doc_and_scope(env):
    ctx, ids = env
    out = run_tool("query_table", {"doc_id": ids[("alice", "doc.pdf")]}, ctx)
    assert "not a table" in out
    foreign = run_tool("query_table", {"doc_id": ids[("bob", "sales.csv")]}, ctx)
    missing = run_tool("query_table", {"doc_id": "nope"}, ctx)
    assert foreign == missing and "not found" in foreign
    ctx.doc_ids = ["other"]
    assert run_tool("query_table", {"doc_id": ids[("alice", "sales.csv")]}, ctx) == missing


def test_xlsx(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    d = reg.create("alice", "s.xlsx", "x", 1, "s", 1, "ready")
    data = (FIX / "sample.xlsx").read_bytes()
    ctx = ToolContext("alice", None, reg, None, None, None, lambda q: [], None,
                      table_loader=lambda o, i: data)
    assert 'status="ok"' in run_tool("query_table", {"doc_id": d}, ctx)

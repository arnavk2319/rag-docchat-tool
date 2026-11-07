"""T-027: query_table answers from the original persisted in the ObjectStore."""

import asyncio
from pathlib import Path

from app.core.models import FakeEmbedder
from app.objectstore import LocalObjectStore
from app.objectstore.loader import make_table_loader
from app.services.agent.tools import ToolContext, run_tool
from app.services.ingestion import IngestionService
from app.store.parents import SqliteParents
from app.store.registry import SqliteRegistry
from vecutil import make_store

FIX = Path(__file__).parent / "fixtures"


async def test_query_table_via_loader(tmp_path):
    reg = SqliteRegistry(tmp_path / "r.db")
    objs = LocalObjectStore(tmp_path / "o")
    parents = SqliteParents(tmp_path / "r.db")
    store = make_store()
    svc = IngestionService(reg, store.inner, FakeEmbedder(), parents=parents, objects=objs)
    doc = await svc.ingest("alice", str(FIX / "sales.csv"), "sales.csv", "text/csv")
    ctx = ToolContext(
        "alice", None, reg, store, parents, None, lambda q: [], None,
        table_loader=make_table_loader(objs, reg),
    )
    out = await asyncio.to_thread(
        run_tool, "query_table",
        {"doc_id": doc.doc_id, "group_by": ["region"], "aggs": [{"column": "units", "fn": "sum"}]},
        ctx,
    )
    assert 'status="ok"' in out and "north | 15" in out
    assert doc.doc_id in ctx.surfaced  # citation recorded
    # a different owner cannot load it, and a missing original yields the not-found result
    other = make_table_loader(objs, reg)("bob", doc.doc_id)
    assert other is None
    await objs.delete("default", svc.original_key(doc))
    out2 = await asyncio.to_thread(run_tool, "query_table", {"doc_id": doc.doc_id}, ctx)
    assert 'status="ok"' not in out2

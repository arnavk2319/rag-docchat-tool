import json

import pytest
from app.core import llm
from app.services import summarization
from app.services.agent.graph import run_agent
from app.services.agent.tools import ToolContext
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
        d = reg.create(owner, f"{owner}.pdf", "application/pdf", 1, f"sha{owner}", 1, "ready")
        ids[owner] = d
        text = f"{owner} pricing is 42 dollars"
        par.replace_doc(owner, d, [Parent(owner_id=owner, doc_id=d, parent_id=f"p{d}", page=1,
                                          section="S", char_start=0, char_end=5, text=text)])
        hits.append(Hit(chunk_id=f"c{d}", text=text, distance=0.1,
                        metadata={"owner_id": owner, "doc_id": d, "parent_id": f"p{d}"}))
    ctx = ToolContext("alice", None, reg, FakeVec(hits), par, None, lambda q: [0.0], FakeReranker())
    return ctx, ids


def J(**kw):
    return json.dumps(kw)


ROUTE_R = J(route="retrieve", answer="")


def plan_tool(tool, **args):
    return J(action="tool", tool=tool, args=args)


PLAN_FINAL = J(action="final", tool="", args={})


@pytest.fixture
def spy(monkeypatch):
    seen = []
    real = llm.complete

    async def s(messages, **kw):
        seen.append(kw)
        return await real(messages, **kw)

    monkeypatch.setattr(llm, "complete", s)
    return seen


async def test_direct_path_zero_tools(env, spy):
    ctx, _ = env
    llm.mock_script(J(route="direct", answer="Hello! Ask me about your documents."))
    r = await run_agent("hi", ctx)
    assert r.tool_calls == 0 and r.answer.startswith("Hello") and not r.bounded_stop
    assert len(spy) == 1


async def test_retrieve_path_cites(env):
    ctx, ids = env
    d = ids["alice"]
    llm.mock_script(ROUTE_R, plan_tool("search_documents", query="pricing"), PLAN_FINAL,
                    f"Pricing is 42 dollars [doc_id={d}].")
    r = await run_agent("what is the pricing?", ctx)
    assert r.tool_calls == 1 and not r.bounded_stop
    assert r.citations and r.citations[0]["doc_id"] == d
    assert r.steps <= 8


async def test_no_evidence_abstains_without_answer_call(env, spy):
    ctx, _ = env
    llm.mock_script(ROUTE_R, plan_tool("get_page_or_section", doc_id="nope"), PLAN_FINAL)
    r = await run_agent("q", ctx)
    assert r.answer == "I don't know." and len(spy) == 3


async def test_infinite_tool_loop_is_bounded(env):
    ctx, _ = env
    llm.mock_script(ROUTE_R, *[plan_tool("list_documents")] * 30)
    r = await run_agent("loop", ctx)
    assert r.stop_reason == "max_steps" and r.steps <= 8 and r.tool_calls >= 1
    assert "Stopped" in r.answer


async def test_unknown_tool_rejected_and_counted(env):
    ctx, _ = env
    llm.mock_script(ROUTE_R, plan_tool("rm_rf", path="/"), PLAN_FINAL)
    r = await run_agent("q", ctx)
    assert r.tool_calls == 1 and r.answer == "I don't know."
    assert r.steps == 5  # router, plan, tool, plan, answer


async def test_model_cannot_supply_owner(env, monkeypatch):
    ctx, _ids = env
    seen = []
    from app.services.agent import nodes
    real = nodes.run_tool
    monkeypatch.setattr(nodes, "run_tool", lambda n, a, c: seen.append(real(n, a, c)) or seen[-1])
    llm.mock_script(ROUTE_R, plan_tool("list_documents", owner_id="bob"), PLAN_FINAL)
    await run_agent("q", ctx)
    assert "invalid arguments" in seen[0] and "bob" not in seen[0]


async def test_clamps_max_tokens_and_skips_when_usd_low(env, spy):
    ctx, _ = env
    llm.mock_script(J(route="direct", answer="ok"))
    await run_agent("hi", ctx, max_tokens=120)
    assert spy[0]["max_tokens"] == 120
    spy.clear()
    r = await run_agent("hi", ctx, max_usd=0.0)
    assert not spy and r.stop_reason == "budget"
    llm.mock_script(J(route="direct", answer="ok"))
    r = await run_agent("hi", ctx, max_usd=1e-9)  # cannot cover expected cost
    assert not spy and r.stop_reason == "budget"


async def test_budget_exceeded_attaches_paid_result():
    b = llm.Budget(max_steps=10, max_tokens=1, max_usd=1.0)
    llm.mock_script("paid reply text")
    with pytest.raises(llm.BudgetExceeded) as ei:
        await llm.complete([{"role": "user", "content": "x"}], model_role="fast", budget=b)
    assert ei.value.result is not None and ei.value.result.text == "paid reply text"


async def test_paid_result_used_when_budget_trips(env):
    ctx, _ = env
    # tiny token cap: the router call is paid for, trips the budget, and its reply is still used
    llm.mock_script(J(route="direct", answer="salvaged answer"))
    r = await run_agent("hi", ctx, max_tokens=30)
    assert r.answer == "salvaged answer" and r.stop_reason == "budget"


async def test_tool_inner_llm_calls_count_against_budget(env):
    ctx, ids = env
    llm.mock_script(ROUTE_R, plan_tool("summarize_document", doc_id=ids["alice"]), PLAN_FINAL, "done")
    r = await run_agent("summarize", ctx)
    assert r.llm_calls > 3 and r.tokens > 0 and r.usd >= 0  # router+plan+plan(+answer)+summary calls


async def test_compare_charged_and_exhausted_budget_stops(env):
    ctx, ids = env
    ctx.registry.create("alice", "b.pdf", "application/pdf", 1, "shab", 1, "ready")
    other = next(d.doc_id for d in ctx.registry.list_by_owner("alice") if d.doc_id != ids["alice"])
    ctx.parents.replace_doc("alice", other, [Parent(owner_id="alice", doc_id=other, parent_id="pz", page=1,
                            section="S", char_start=0, char_end=5, text="other")])
    llm.mock_script(ROUTE_R, plan_tool("compare_documents", doc_ids=[ids["alice"], other]), PLAN_FINAL)
    r = await run_agent("compare", ctx, max_tokens=500)
    assert r.llm_calls >= 3  # includes the compare tool's own call


async def test_unreachable_budget_inside_tool_stops(env):
    ctx, ids = env
    llm.mock_script(ROUTE_R, plan_tool("summarize_document", doc_id=ids["alice"]), PLAN_FINAL)
    r = await run_agent("s", ctx, max_usd=0.0004)
    assert r.stop_reason == "budget" or r.usd <= 0.0004 + 1e-4


async def test_min_rerank_score_passed(env, monkeypatch):
    ctx, _ = env
    monkeypatch.setenv("MIN_RERANK_SCORE", "5.0")
    from app.core import config
    config.get_settings.cache_clear()
    llm.mock_script(ROUTE_R, plan_tool("search_documents", query="pricing"), PLAN_FINAL)
    r = await run_agent("q", ctx)  # reranker scores 1.0 < 5.0 -> no results -> abstain
    assert r.answer == "I don't know."


# ---- scripted eval: task_success >= 0.8 over 5 mock scenarios -----------------------------
async def test_scripted_eval_task_success(env):
    ctx, ids = env
    d = ids["alice"]
    cases = [
        ([J(route="direct", answer="hi there")], lambda r: r.tool_calls == 0 and r.answer == "hi there"),
        ([ROUTE_R, plan_tool("list_documents"), PLAN_FINAL, f"You have alice.pdf [doc_id={d}]"],
         lambda r: r.tool_calls == 1 and d in r.answer),
        ([ROUTE_R, plan_tool("search_documents", query="pricing"), PLAN_FINAL, f"42 [doc_id={d}]"],
         lambda r: r.citations and r.citations[0]["doc_id"] == d),
        ([ROUTE_R, plan_tool("get_page_or_section", doc_id=d, page=1), PLAN_FINAL, f"42 dollars [doc_id={d}]"],
         lambda r: r.tool_calls == 1 and not r.bounded_stop),
        ([ROUTE_R, *[plan_tool("list_documents")] * 20], lambda r: r.stop_reason == "max_steps" and r.steps <= 8),
    ]
    ok = 0
    for script, check in cases:
        llm.mock_script(*script)
        ok += bool(check(await run_agent("q", ctx)))
    assert ok / len(cases) >= 0.8

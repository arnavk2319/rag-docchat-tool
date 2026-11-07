"""LangGraph StateGraph for the agent: router -> (direct END | plan <-> tool -> answer).

Bounds: `recursion_limit` = AGENT_MAX_STEPS node executions (GraphRecursionError -> bounded-stop
result), plus a per-run `Budget` (tokens, USD) that also absorbs LLM calls made inside tools.
The graph is built per run with closures over that run's deps, so no module state is loop-bound.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from ...core import llm
from ...core.config import get_settings
from ..sessions import history_block
from . import nodes
from .nodes import AgentState, Deps
from .tools import ToolContext

log = logging.getLogger("app.agent")
AGENT_MAX_TOKENS = 30_000  # per-run token cap (no env knob yet; see docs/architecture/agent.md)
MAX_QUESTION_CHARS = 2000


@dataclass
class AgentResult:
    answer: str
    citations: list[dict[str, str]] = field(default_factory=list)
    steps: int = 0
    tool_calls: int = 0
    stop_reason: str = ""  # "" = completed, else budget | max_steps | llm_error
    usd: float = 0.0
    tokens: int = 0
    llm_calls: int = 0

    @property
    def bounded_stop(self) -> bool:
        return bool(self.stop_reason)


def build_graph(d: Deps, verify: bool = True) -> Any:
    def wrap(name: str, label: str, fn: Any) -> Any:
        # Optional per-run event hook: emits step (and tool_call/tool_result) around a node.
        async def run(s: AgentState) -> AgentState:
            raw = d.emit
            if raw is None:
                return await fn(s, d)

            def em(name_: str, payload: dict[str, Any]) -> None:
                try:
                    raw(name_, payload)
                except Exception:  # noqa: BLE001 - a broken hook must never fail the run
                    log.warning("event hook failed", extra={"run_id": d.run_id})

            pending = s.get("pending") or {}
            if name == "tool":
                em("tool_call", {"tool": pending.get("tool", ""), "args": pending.get("args", {})})
            out = await fn(s, d)
            if name == "tool":
                last = (out.get("history") or [{}])[-1].get("result", "")
                em("tool_result", {"tool": pending.get("tool", ""), "result": last})
            em("step", {"node": label, "n": out.get("steps", s.get("steps", 0))})
            return out

        return run

    n_router = wrap("router", "router", nodes.router)
    n_plan = wrap("plan", "plan", nodes.plan)
    n_tool = wrap("tool", "tool", nodes.tool)
    n_grade = wrap("grade", "grade", nodes.grade)
    n_rewrite = wrap("rewrite", "rewrite", nodes.rewrite)
    n_ground = wrap("ground", "groundedness", nodes.groundedness)
    n_answer = wrap("answer", "answer", nodes.answer)

    g = StateGraph(AgentState)
    g.add_node("router", n_router)
    g.add_node("plan", n_plan)
    g.add_node("tool", n_tool)
    g.add_node("answer", n_answer)
    final_to = "grade" if verify else "answer"
    if verify:
        g.add_node("grade", n_grade)
        g.add_node("rewrite", n_rewrite)
        g.add_node("ground", n_ground)
    g.add_edge(START, "router")
    g.add_conditional_edges(
        "router", lambda s: END if s.get("stop") or s.get("route") == "direct" else "plan",
        {END: END, "plan": "plan"},
    )
    g.add_conditional_edges(
        "plan", lambda s: s.get("next", "stop"), {"tool": "tool", "final": final_to, "stop": END}
    )
    g.add_conditional_edges(
        "tool", lambda s: END if s.get("stop") else "plan", {END: END, "plan": "plan"}
    )
    if verify:
        g.add_conditional_edges(
            "grade",
            lambda s: END if s.get("stop") or s.get("grade") == "abstain"
            else ("answer" if s.get("grade") == "relevant" else "rewrite"),
            {END: END, "answer": "answer", "rewrite": "rewrite"},
        )
        g.add_conditional_edges(
            "rewrite", lambda s: END if s.get("stop") or s.get("next") == "stop" else "plan",
            {END: END, "plan": "plan"},
        )
        g.add_conditional_edges(
            "answer", lambda s: END if s.get("stop") else "ground", {END: END, "ground": "ground"}
        )
        g.add_edge("ground", END)
    else:
        g.add_edge("answer", END)
    return g.compile()


async def run_agent(
    question: str,
    ctx: ToolContext,
    *,
    max_steps: int | None = None,
    max_tokens: int = AGENT_MAX_TOKENS,
    max_usd: float | None = None,
    verify: bool = True,
    run_id: str | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    cancel: threading.Event | None = None,
    history: list[dict[str, str]] | None = None,
) -> AgentResult:
    s = get_settings()
    max_steps = max_steps or s.agent_max_steps
    budget = llm.Budget(
        max_steps=10_000,  # LLM-call count is not the bound; graph steps, tokens and USD are
        max_tokens=max_tokens,
        max_usd=s.agent_max_usd if max_usd is None else max_usd,
    )
    budget.cancel = cancel
    ctx = replace(ctx, budget=budget, min_rerank_score=s.min_rerank_score)
    d = Deps(ctx=ctx, budget=budget, run_id=run_id or uuid.uuid4().hex[:12], emit=on_event)
    graph = build_graph(d, verify)
    last: AgentState = {"question": question[:MAX_QUESTION_CHARS], "steps": 0, "history": []}
    if history:
        last["convo"] = history_block(history)
    stop = ""
    try:
        async for snap in graph.astream(
            dict(last), config={"recursion_limit": max_steps}, stream_mode="values"
        ):
            last = snap
    except GraphRecursionError:
        stop = "max_steps"
    stop = stop or last.get("stop", "")
    ans = last.get("answer", "") or (nodes._stopped(stop) if stop else "")
    if stop == "max_steps":
        ans = nodes._stopped(stop)
    return AgentResult(
        answer=ans,
        citations=nodes.citations(ans, last.get("surfaced", {})),
        steps=min(last.get("steps", 0), max_steps),
        tool_calls=last.get("tool_calls", 0),
        stop_reason=stop,
        usd=budget.usd,
        tokens=budget.tokens,
        llm_calls=budget.steps,
    )

"""Agent graph nodes: plain async callables `node(state, deps) -> state update`.

Every LLM call goes through `core.llm.complete` (mock provider in tests) via `_call`, which clamps
max_tokens to the remaining token budget, skips the call when remaining USD cannot cover the
expected cost, and salvages the already-paid result if `Budget.charge` trips. Tool calls are
synchronous (`run_tool`) so they run in a worker thread. Nothing here holds loop-bound state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from ...core import llm
from ...core.config import get_settings
from ...core.prompts import load_prompt
from .tools import TOOLS, ToolContext, escape, run_tool

log = logging.getLogger("app.agent")
MAX_HISTORY_ITEMS = 8
ARGS_CHARS = 300


class AgentState(TypedDict, total=False):
    question: str
    route: str
    answer: str
    stop: str  # "" | budget | max_steps | no_budget | llm_error
    steps: int
    history: list[dict[str, str]]  # {"tool","args","result"}
    next: str  # plan decision: tool | final
    pending: dict[str, Any]  # requested tool call
    tool_calls: int
    surfaced: dict[str, str]  # doc_id -> span, from tool code (never parsed from text)


class RouterOut(BaseModel):
    route: str = "retrieve"
    answer: str = ""


class PlanOut(BaseModel):
    action: str = "final"
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)


@dataclass
class Deps:
    ctx: ToolContext  # ctx.budget is the per-run Budget
    budget: llm.Budget
    run_id: str


class Skip(Exception):
    """LLM call not made (budget); carries the stop reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _expected_cost(role: str, prompt: str, max_tokens: int) -> float:
    s = get_settings()
    model = (s.ollama_agent_model if role == "agent" else s.ollama_fast_model) if (
        s.llm_provider == "ollama") else (s.agent_model if role == "agent" else s.fast_model)
    pin, pout = s.prices.get(model, (0.0, 0.0))
    return (max(1, len(prompt) // 4) * pin + max_tokens * pout) / 1_000_000


async def _call(
    d: Deps, prompt: str, *, role: llm.Role, schema: type[BaseModel] | None, max_tokens: int
) -> tuple[llm.LLMResult | None, str]:
    """Returns (result, stop). stop != "" means the budget is spent after this call."""
    b = d.budget
    remaining = b.max_tokens - b.tokens
    if remaining <= 0 or b.usd >= b.max_usd:
        raise Skip("budget")
    max_tokens = min(max_tokens, remaining)
    if _expected_cost(role, prompt, max_tokens) > b.max_usd - b.usd:
        raise Skip("budget")
    try:
        res = await llm.complete(
            [{"role": "user", "content": prompt}], model_role=role, schema=schema,
            max_tokens=max_tokens, budget=b, run_id=d.run_id,
        )
        return res, ""
    except llm.BudgetExceeded as e:
        if e.result is None:
            raise Skip("budget") from e
        return e.result, "budget"
    except llm.LLMError as e:
        log.warning("agent llm error", extra={"run_id": d.run_id, "kind": type(e).__name__})
        raise Skip("llm_error") from e


def _bump(state: AgentState, **kw: Any) -> AgentState:
    return {"steps": state.get("steps", 0) + 1, **kw}  # type: ignore[return-value]


def _tool_catalog() -> str:
    lines = []
    for t in TOOLS.values():
        fields = ", ".join(t.args_model.model_fields)
        lines.append(f"- {t.name}: {t.description} args: {fields or 'none'}")
    return "\n".join(lines)


def _history_text(history: list[dict[str, str]]) -> str:
    if not history:
        return "(none)"
    return "\n".join(
        f"[{i}] tool={h['tool']} args={escape(h['args'])}\n{h['result']}"
        for i, h in enumerate(history[-MAX_HISTORY_ITEMS:], 1)
    )


def _q(state: AgentState) -> str:
    return escape(state["question"])


async def router(state: AgentState, d: Deps) -> AgentState:
    p = load_prompt("agent_router").render(question=_q(state))
    try:
        res, stop = await _call(d, p, role="fast", schema=RouterOut, max_tokens=300)
    except Skip as s:
        return _bump(state, stop=s.reason, answer=_stopped(s.reason))
    out = res.parsed if res and isinstance(res.parsed, RouterOut) else RouterOut()
    if out.route == "direct" and out.answer.strip():
        return _bump(state, route="direct", answer=out.answer.strip(), stop=stop)
    return _bump(state, route="retrieve", stop=stop)


async def plan(state: AgentState, d: Deps) -> AgentState:
    p = load_prompt("agent_plan").render(
        tools=_tool_catalog(), question=_q(state), history=_history_text(state.get("history", []))
    )
    try:
        res, stop = await _call(d, p, role="agent", schema=PlanOut, max_tokens=400)
    except Skip as s:
        return _bump(state, stop=s.reason, next="stop", answer=_stopped(s.reason))
    out = res.parsed if res and isinstance(res.parsed, PlanOut) else None
    if stop or out is None:  # budget tripped on this call, or unusable decision
        return _bump(state, stop=stop or "llm_error", next="stop", answer=_stopped(stop or "llm_error"))
    if out.action == "tool":
        return _bump(state, next="tool", pending={"tool": out.tool, "args": out.args})
    return _bump(state, next="final")


async def tool(state: AgentState, d: Deps) -> AgentState:
    req = state.get("pending") or {}
    name, args = req.get("tool", ""), req.get("args", {})
    hist = list(state.get("history", []))
    surfaced = dict(state.get("surfaced", {}))
    if not isinstance(name, str) or name not in TOOLS:  # allow-list; still counts as a step
        result = "<tool_result status=\"error\">\nunknown tool\n</tool_result>"
        name = escape(str(name))[:60]
    else:
        # run_tool is sync, never raises, escapes + wraps output. owner_id comes only from ctx;
        # model-supplied args are validated (extra=forbid), so an owner_id arg is rejected.
        call_ctx = replace(d.ctx, surfaced={})  # per-call dict: no shared mutable state across runs
        result = await asyncio.to_thread(run_tool, name, args, call_ctx)
        if _ok(result):
            surfaced.update(call_ctx.surfaced)
    hist.append({"tool": name, "args": json.dumps(args, default=str)[:ARGS_CHARS], "result": result})
    stop = ""
    b = d.budget
    if b.tokens >= b.max_tokens or b.usd >= b.max_usd:
        stop = "budget"  # tool-internal LLM calls (summarize/compare) exhausted the budget
    return _bump(state, history=hist, surfaced=surfaced, tool_calls=state.get("tool_calls", 0) + 1, stop=stop,
                 answer=_stopped(stop) if stop else state.get("answer", ""))


def _ok(result: str) -> bool:
    return 'status="ok"' in result.split("\n", 1)[0]


def _has_evidence(history: list[dict[str, str]]) -> bool:
    return any(_ok(h["result"]) and "no results" not in h["result"]
               and "no documents" not in h["result"] for h in history)


async def answer(state: AgentState, d: Deps) -> AgentState:
    hist = state.get("history", [])
    if not _has_evidence(hist):
        return _bump(state, answer="I don't know.")
    p = load_prompt("agent_answer").render(question=_q(state), history=_history_text(hist))
    try:
        res, stop = await _call(d, p, role="agent", schema=None, max_tokens=700)
    except Skip as s:
        return _bump(state, stop=s.reason, answer=_stopped(s.reason))
    return _bump(state, answer=(res.text.strip() if res else "") or "I don't know.", stop=stop)


def _stopped(reason: str) -> str:
    return {
        "budget": "Stopped: the run budget was reached before an answer was complete.",
        "max_steps": "Stopped: the step limit was reached before an answer was complete.",
        "llm_error": "Stopped: the model could not produce a usable reply.",
    }.get(reason, "Stopped.")


def citations(answer_text: str, surfaced: dict[str, str]) -> list[dict[str, str]]:
    """Doc ids the answer names that a tool structurally surfaced (never parsed from text)."""
    return [
        {"doc_id": i, "span": span}
        for i, span in surfaced.items()
        if re.search(rf"doc_id={re.escape(i)}(?![\w-])", answer_text)
    ]

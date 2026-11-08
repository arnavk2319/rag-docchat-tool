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
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from ...core import llm
from ...core.config import get_settings
from ...core.prompts import load_prompt
from ...core.security import escape, sanitize_untrusted
from .tools import TOOLS, ToolContext, run_tool

log = logging.getLogger("app.agent")
MAX_HISTORY_ITEMS = 8
ARGS_CHARS = 300
MAX_REWRITES = 1  # cheaper default (T-023): one rewrite, then abstain
MAX_REGENERATE = 1
REWRITE_CHARS = 200
STRICT_NOTE = (
    "- A previous draft contained claims not supported by the tool results. "
    "State only facts that appear verbatim in them; omit anything else.\n"
)


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
    surfaced_chunks: dict[str, str]  # chunk_id -> doc_id, from tool code (never parsed from text)
    surfaced: dict[str, str]  # doc_id -> span, from tool code (never parsed from text)
    rewrites: int  # rewrite loops used (<= MAX_REWRITES)
    rewrite: str  # rewritten search query (LLM output: untrusted data)
    grade: str  # "relevant" | "irrelevant" | "abstain"
    regenerated: int  # answer regenerations used (<= MAX_REGENERATE)
    convo: str  # pre-escaped <conversation> block of earlier session turns (data), or ""


class RouterOut(BaseModel):
    route: str = "retrieve"
    answer: str = ""


class PlanOut(BaseModel):
    action: str = "final"
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)


class GradeOut(BaseModel):
    relevant: bool = False


class RewriteOut(BaseModel):
    query: str = ""


class GroundedOut(BaseModel):
    grounded: bool = False


@dataclass
class Deps:
    ctx: ToolContext  # ctx.budget is the per-run Budget
    budget: llm.Budget
    run_id: str
    emit: Callable[[str, dict[str, Any]], None] | None = None  # per-run event hook (SSE); sync, non-blocking


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
        f"[{i}] tool={h['tool']} args={sanitize_untrusted(h['args'])}\n{h['result']}"
        for i, h in enumerate(history[-MAX_HISTORY_ITEMS:], 1)
    )


def _q(state: AgentState) -> str:
    return state.get("convo", "") + sanitize_untrusted(state["question"])


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
    q = _q(state)
    if state.get("rewrite"):
        q += f"\nSuggested search query (data): {sanitize_untrusted(state['rewrite'])}"
    p = load_prompt("agent_plan").render(
        tools=_tool_catalog(), question=q, history=_history_text(state.get("history", []))
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
    chunks = dict(state.get("surfaced_chunks", {}))
    if not isinstance(name, str) or name not in TOOLS:  # allow-list; still counts as a step
        result = "<tool_result status=\"error\">\nunknown tool\n</tool_result>"
        name = escape(str(name))[:60]
    else:
        # run_tool is sync, never raises, escapes + wraps output. owner_id comes only from ctx;
        # model-supplied args are validated (extra=forbid), so an owner_id arg is rejected.
        # per-call dicts: no shared mutable state across runs
        call_ctx = replace(d.ctx, surfaced={}, surfaced_chunks={})
        result = await asyncio.to_thread(run_tool, name, args, call_ctx)
        if _ok(result):
            surfaced.update(call_ctx.surfaced)
            chunks.update(call_ctx.surfaced_chunks)
    hist.append({"tool": name, "args": json.dumps(args, default=str)[:ARGS_CHARS], "result": result})
    stop = ""
    b = d.budget
    if b.tokens >= b.max_tokens or b.usd >= b.max_usd:
        stop = "budget"  # tool-internal LLM calls (summarize/compare) exhausted the budget
    return _bump(state, history=hist, surfaced=surfaced, surfaced_chunks=chunks,
                 tool_calls=state.get("tool_calls", 0) + 1, stop=stop,
                 answer=_stopped(stop) if stop else state.get("answer", ""))


def _ok(result: str) -> bool:
    return 'status="ok"' in result.split("\n", 1)[0]


def _has_evidence(history: list[dict[str, str]]) -> bool:
    return any(_ok(h["result"]) and "no results" not in h["result"]
               and "no documents" not in h["result"] for h in history)


async def _generate(state: AgentState, d: Deps, strict: str) -> tuple[str, str]:
    p = load_prompt("agent_answer").render(
        question=_q(state), history=_history_text(state.get("history", [])), strict=strict
    )
    res, stop = await _call(d, p, role="agent", schema=None, max_tokens=700)
    return (res.text.strip() if res else "") or "I don't know.", stop


async def answer(state: AgentState, d: Deps) -> AgentState:
    if not _has_evidence(state.get("history", [])):
        return _bump(state, answer="I don't know.")
    try:
        text, stop = await _generate(state, d, "")
    except Skip as s:
        return _bump(state, stop=s.reason, answer=_stopped(s.reason))
    return _bump(state, answer=text, stop=stop)


async def _judge(
    d: Deps, prompt: str, schema: type[BaseModel], max_tokens: int
) -> tuple[BaseModel | None, str]:
    """Structured fast-tier call. Returns (parsed or None, stop). A malformed reply (after the
    wrapper's repair retry) yields None, which every caller treats as the safe negative."""
    try:
        res, stop = await _call(d, prompt, role="fast", schema=schema, max_tokens=max_tokens)
    except Skip as s:
        if s.reason == "llm_error":
            return None, ""  # includes LLMValidationError: fail safe, never loop
        raise
    parsed = res.parsed if res and isinstance(res.parsed, schema) else None
    return parsed, stop


async def grade(state: AgentState, d: Deps) -> AgentState:
    """Is the gathered evidence relevant? No evidence is irrelevant without an LLM call."""
    hist = state.get("history", [])
    rewrites = state.get("rewrites", 0)
    relevant, stop = False, ""
    if _has_evidence(hist):
        p = load_prompt("agent_grade").render(question=_q(state), history=_history_text(hist))
        try:
            out, stop = await _judge(d, p, GradeOut, 100)
        except Skip as s:
            return _bump(state, stop=s.reason, answer=_stopped(s.reason))
        if out is None:  # malformed: abstain, do not burn rewrites on a broken grader
            return _bump(state, grade="abstain", answer="I don't know.", stop=stop)
        relevant = isinstance(out, GradeOut) and out.relevant
    elif not hist:  # nothing was even tried; a rewrite has nothing to improve on
        return _bump(state, grade="relevant")
    if relevant:
        return _bump(state, grade="relevant", stop=stop)
    if rewrites >= MAX_REWRITES or stop:
        return _bump(state, grade="abstain", answer="I don't know.", stop=stop)
    return _bump(state, grade="irrelevant")


async def rewrite(state: AgentState, d: Deps) -> AgentState:
    p = load_prompt("agent_rewrite").render(
        question=_q(state), history=_history_text(state.get("history", []))
    )
    try:
        out, stop = await _judge(d, p, RewriteOut, 120)
    except Skip as s:
        return _bump(state, stop=s.reason, answer=_stopped(s.reason))
    q = out.query.strip()[:REWRITE_CHARS] if isinstance(out, RewriteOut) else ""
    if not q or stop:
        return _bump(state, grade="abstain", answer="I don't know.", stop=stop, next="stop")
    # fresh evidence for the retry: drop irrelevant results and their citations
    return _bump(state, rewrite=q, rewrites=state.get("rewrites", 0) + 1, history=[], surfaced={},
                 surfaced_chunks={})


async def groundedness(state: AgentState, d: Deps) -> AgentState:
    """Check the draft against the evidence; one regenerate on failure, then abstain."""
    hist = state.get("history", [])
    draft = state.get("answer", "")
    if state.get("stop") or draft.strip() == "I don't know.":
        return _bump(state)
    regenerated = state.get("regenerated", 0)
    stop = ""
    for attempt in range(MAX_REGENERATE + 1):
        p = load_prompt("agent_grounded").render(
            question=_q(state), history=_history_text(hist), draft=sanitize_untrusted(draft)
        )
        try:
            out, stop = await _judge(d, p, GroundedOut, 100)
        except Skip as s:
            return _bump(state, stop=s.reason, answer=_stopped(s.reason), regenerated=regenerated)
        if isinstance(out, GroundedOut) and out.grounded:
            return _bump(state, answer=draft, stop=stop, regenerated=regenerated)
        if attempt >= MAX_REGENERATE or stop:
            break
        regenerated += 1
        try:
            draft, stop = await _generate(state, d, STRICT_NOTE)
        except Skip as s:
            return _bump(state, stop=s.reason, answer=_stopped(s.reason), regenerated=regenerated)
        if stop:
            break
    return _bump(state, answer="I don't know.", stop=stop, regenerated=regenerated)


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

"""SSE streaming for POST /v1/query (ADR-0002 section 4).

The agent runs in its own task and pushes raw events into a per-request queue; this module
sanitises them (escape, allow-listed fields, caller's doc scope), numbers them (`seq`) and frames
them. Disconnect or generator close cancels the task, so no further LLM calls are made. After the
stream opens nothing raises: faults become `error` + `final` events with fixed codes, no text.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import Request

from ..core.config import get_settings
from ..core.security import sanitize_output
from ..services.agent.graph import AgentResult, run_agent
from ..services.agent.tools import TOOLS, ToolContext, escape

log = logging.getLogger("app.sse")
HEARTBEAT_S = 15.0
POLL_S = 1.0  # disconnect poll interval
IDK = "I don't know."
STEP_SUMMARY = {
    "router": "Choosing how to answer",
    "plan": "Planning the next action",
    "tool": "Ran a tool",
    "grade": "Checking the evidence",
    "rewrite": "Rewriting the search query",
    "answer": "Drafting the answer",
    "groundedness": "Verifying the answer against the evidence",
}
_WORDS = re.compile(r"\S+\s*")
_ARG_CHARS = 200


def _clean(v: Any) -> Any:
    if isinstance(v, str):
        return escape(v)
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    return v


def _safe_args(tool: str, raw: Any, allowed: set[str]) -> dict[str, Any]:
    t = TOOLS.get(tool)
    if t is None:
        return {}
    try:
        args = t.args_model.model_validate(raw if raw is not None else {}).model_dump(exclude_none=True)
    except Exception:  # noqa: BLE001 - invalid args are simply not shown
        return {}
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k == "doc_id":
            if v in allowed:
                out[k] = v
        elif k == "doc_ids" and isinstance(v, list):
            out[k] = [x for x in v if x in allowed]
        elif isinstance(v, str):
            out[k] = v[:_ARG_CHARS]
        elif isinstance(v, bool | int | float):
            out[k] = v
    return out


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _n_items(result: str) -> int:
    lines = result.split("\n")[1:-1]
    return sum(1 for ln in lines if ln.strip())


def sanitize(name: str, p: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    if name == "step":
        node = p.get("node")
        if not isinstance(node, str) or node not in STEP_SUMMARY:  # allow-list; unknown -> fixed label
            return {"node": "step", "n": _int(p.get("n")), "summary": ""}
        return {"node": node, "n": _int(p.get("n")), "summary": STEP_SUMMARY[node]}
    if name == "tool_call":
        tool = p.get("tool", "")
        tool = tool if tool in TOOLS else "unknown"
        return {"tool": tool, "args": _clean(_safe_args(tool, p.get("args"), allowed))}
    if name == "tool_result":
        tool = p.get("tool", "")
        res = str(p.get("result", ""))
        return {
            "tool": tool if tool in TOOLS else "unknown",
            "ok": 'status="ok"' in res.split("\n", 1)[0],
            "n_items": _n_items(res),
        }
    return {}


def stop_reason(r: AgentResult) -> str:
    sr = r.stop_reason
    if sr == "max_steps":
        return "max_steps"
    if sr in ("budget", "no_budget"):
        return "max_usd" if r.usd >= get_settings().agent_max_usd else "max_tokens"
    if sr == "llm_error":
        return "error"
    if r.answer.strip() == IDK:
        return "abstained"
    return "done" if sr == "" else "error"  # unknown reasons never report success


def frame(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))  # one line
    return f"event: {event}\ndata: {data}\n\n"


async def stream_agent(
    request: Request,
    question: str,
    ctx: ToolContext,
    names: dict[str, str],
    *,
    history: list[dict[str, str]] | None = None,
    session_id: str | None = None,
    on_complete: Callable[[str, list[str]], Awaitable[None]] | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames. `names` = caller's ready docs (doc_id -> filename); empty = no run."""
    run_id = uuid.uuid4().hex[:12]
    allowed = set(names)
    seq = 0

    def ev(name: str, payload: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        return frame(name, {"run_id": run_id, "seq": seq, **payload})

    def final(answer: str, cites: list[Any], stopped: str, usage: dict[str, Any]) -> str:
        return ev("final", {
            "answer": answer, "citations": cites, "abstained": answer.strip() == IDK,
            "stopped_reason": stopped, "usage": usage,
            **({"session_id": session_id} if session_id is not None else {}),
        })

    zero = {"steps": 0, "tokens": 0, "usd": 0.0}
    if not allowed:
        yield ev("token", {"text": IDK})
        yield ev("citations", {"citations": []})
        yield final(IDK, [], "abstained", zero)
        return

    cancel = threading.Event()  # per run; stops worker-thread tools from starting new LLM calls
    q: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    async def runner() -> AgentResult:
        try:
            return await run_agent(
                question, ctx, run_id=run_id, on_event=lambda n, p: q.put_nowait((n, p)),
                cancel=cancel,
                history=history,
            )
        finally:
            q.put_nowait(("", {}))  # sentinel: wakes the consumer immediately

    task = asyncio.create_task(runner())
    last_sent = last_poll = time.monotonic()
    try:
        while True:
            if time.monotonic() - last_poll >= POLL_S:
                last_poll = time.monotonic()
                if await request.is_disconnected():
                    return
            try:
                name, payload = await asyncio.wait_for(q.get(), timeout=POLL_S)
            except TimeoutError:
                if time.monotonic() - last_sent >= HEARTBEAT_S:
                    last_sent = time.monotonic()
                    yield ": ping\n\n"
                continue
            if not name:
                break
            last_sent = time.monotonic()
            yield ev(name, sanitize(name, payload, allowed))
        try:
            res = task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("agent stream failed", extra={"run_id": run_id})
            yield ev("error", {"code": "internal", "detail": "The run failed."})
            yield final("", [], "error", zero)
            return
        text = sanitize_output(res.answer)
        for piece in _WORDS.findall(text):
            yield ev("token", {"text": piece})
        cites = [
            {"doc_id": c["doc_id"], "filename": escape(names[c["doc_id"]]), "span": sanitize_output(c["span"])}
            for c in res.citations if c["doc_id"] in allowed
        ]
        yield ev("citations", {"citations": cites})
        usage = {"steps": res.steps, "tokens": res.tokens, "usd": round(res.usd, 6)}
        stopped = stop_reason(res)
        if stopped == "done" and on_complete is not None:
            try:  # memory is best-effort: a store fault must not break a finished answer
                await on_complete(res.answer, [c["doc_id"] for c in cites])
            except Exception:
                log.exception("session store failed", extra={"run_id": run_id})
        if stopped == "error":
            yield ev("error", {"code": "llm_unavailable", "detail": "The model could not produce a reply."})
        yield final(text, cites, stopped, usage)
    finally:
        cancel.set()
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

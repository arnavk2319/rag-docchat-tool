"""T-019: agent SSE on POST /v1/query?stream=true (real app, fake models, mock LLM)."""

import asyncio
import json

import pytest
from app.api import sse
from app.core import llm
from app.main import app
from authutil import bearer
from fastapi.testclient import TestClient

A = bearer("alice")
B = bearer("bob")


def J(**kw):
    return json.dumps(kw)


ROUTE = J(route="retrieve", answer="")
SEARCH = J(action="tool", tool="search_documents", args={"query": "sky colour"})
FINAL = J(action="final", tool="", args={})
YES, GOOD = J(relevant=True), J(grounded=True)


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def doc(c, headers=A, text=b"The sky colour is blue. Grass is green."):
    r = c.post("/v1/documents", files=[("files", ("a.txt", text))], headers=headers)
    return r.json()["results"][0]["doc_id"]


def parse(text: str):
    events = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        lines = block.split("\n")
        assert lines[0].startswith("event: ") and lines[1].startswith("data: ") and len(lines) == 2
        events.append((lines[0][7:], json.loads(lines[1][6:])))
    return events


def stream(c, body, headers=A):
    return c.post("/v1/query", json={"stream": True, **body}, headers=headers)


def test_scripted_run_order_and_final(client):
    d = doc(client)
    llm.mock_script(ROUTE, SEARCH, FINAL, YES, f"The sky is blue [doc_id={d}] <b>x</b>", GOOD)
    r = stream(client, {"question": "sky colour?"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    ev = parse(r.text)
    names = [n for n, _ in ev]
    assert set(names) <= {"step", "tool_call", "tool_result", "token", "citations", "final", "error"}
    assert names[-1] == "final" and names[-2] == "citations" and "token" in names
    assert names.index("tool_call") < names.index("tool_result") < names.index("token")
    assert [p["seq"] for _, p in ev] == list(range(1, len(ev) + 1))
    assert len({p["run_id"] for _, p in ev}) == 1
    steps = [p["node"] for n, p in ev if n == "step"]
    assert steps[:2] == ["router", "plan"] and "groundedness" in steps
    call = next(p for n, p in ev if n == "tool_call")
    assert call["tool"] == "search_documents" and call["args"]["query"] == "sky colour"
    res = next(p for n, p in ev if n == "tool_result")
    assert res["ok"] is True and set(res) == {"run_id", "seq", "tool", "ok", "n_items"}
    fin = ev[-1][1]
    assert fin["stopped_reason"] == "done" and fin["abstained"] is False
    assert fin["citations"][0]["doc_id"] == d and fin["citations"][0]["filename"] == "a.txt"
    assert set(fin["usage"]) == {"steps", "tokens", "usd"}
    assert "<b>" not in r.text and "&lt;b&gt;" in fin["answer"]
    assert "".join(p["text"] for n, p in ev if n == "token") == fin["answer"]
    assert "alice" not in r.text and "prompt" not in r.text.lower()


def test_budget_and_step_stops_are_final_not_500(client, monkeypatch):
    d = doc(client)
    monkeypatch.setenv("AGENT_MAX_STEPS", "2")
    from app.core import config

    config.get_settings.cache_clear()
    llm.mock_script(ROUTE, SEARCH, SEARCH, SEARCH, SEARCH)
    r = stream(client, {"question": "sky?", "doc_ids": [d]})
    assert r.status_code == 200
    fin = parse(r.text)[-1]
    assert fin[0] == "final" and fin[1]["stopped_reason"] == "max_steps"

    monkeypatch.setenv("AGENT_MAX_USD", "0")
    config.get_settings.cache_clear()
    llm.mock_script(ROUTE)
    r = stream(client, {"question": "sky?"})
    assert r.status_code == 200
    fin = parse(r.text)[-1]
    assert fin[0] == "final" and fin[1]["stopped_reason"] == "max_usd"


def test_llm_failure_is_error_then_final(client):
    doc(client)
    llm.mock_script(llm.LLMTransportError("secret internals"))
    r = stream(client, {"question": "sky?"})
    ev = parse(r.text)
    assert r.status_code == 200 and "secret internals" not in r.text
    assert [n for n, _ in ev][-2:] == ["error", "final"]
    assert ev[-1][1]["stopped_reason"] == "error"


def test_errors_before_stream_are_plain_json(client):
    doc(client)
    assert client.post("/v1/query", json={"question": "x", "stream": True}).status_code == 401
    r = stream(client, {"question": ""})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/json")
    r = stream(client, {"question": "sky?", "doc_ids": ["nope"]})
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_owner_scoping_no_leak(client):
    d = doc(client)
    r = stream(client, {"question": "sky?", "doc_ids": [d]}, headers=B)
    assert r.status_code == 404 and d not in r.text.replace(r.json()["detail"], "")
    # bob with no docs of his own: stream opens, abstains, never touches alice's data
    llm.mock_script()
    r = stream(client, {"question": "sky colour?"}, headers=B)
    ev = parse(r.text)
    assert ev[-1][1]["abstained"] is True and ev[-1][1]["citations"] == []
    assert d not in r.text


def test_non_stream_unchanged(client):
    doc(client)
    r = client.post("/v1/query", json={"question": "What colour is the sky?"}, headers=A)
    assert r.status_code == 200 and set(r.json()) >= {"answer", "citations", "abstained"}
    assert r.headers["content-type"].startswith("application/json")


class FakeRequest:
    def __init__(self, after: int):
        self.calls, self.after = 0, after

    async def is_disconnected(self):
        self.calls += 1
        return self.calls > self.after


async def test_disconnect_cancels_run_and_stops_llm_calls(monkeypatch):
    calls = {"n": 0}
    started = asyncio.Event()

    async def slow(messages, **kw):
        calls["n"] += 1
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(llm, "complete", slow)
    monkeypatch.setattr(sse, "POLL_S", 0.01)
    from app.services.agent.tools import ToolContext

    ctx = ToolContext("alice", ["d1"], None, None, None, None, lambda q: [0.0], None)
    gen = sse.stream_agent(FakeRequest(after=3), "q?", ctx, {"d1": "a.txt"})
    out = [f async for f in gen]  # ends because the client "disconnected"
    assert started.is_set() and calls["n"] == 1 and out == []
    await asyncio.sleep(0.05)
    assert calls["n"] == 1  # no further LLM calls after cancel
    # match the coroutine's own name, not repr(t): on CI the checkout path contains
    # "/runner/" and would match the test's own task.
    leaked = [
        t
        for t in asyncio.all_tasks()
        if t.get_coro().__qualname__.endswith("stream_agent.<locals>.runner") and not t.done()
    ]
    assert not leaked


async def test_heartbeat_ping(monkeypatch):
    async def slow(messages, **kw):
        await asyncio.sleep(30)

    monkeypatch.setattr(llm, "complete", slow)
    monkeypatch.setattr(sse, "POLL_S", 0.01)
    monkeypatch.setattr(sse, "HEARTBEAT_S", 0.03)
    from app.services.agent.tools import ToolContext

    ctx = ToolContext("alice", ["d1"], None, None, None, None, lambda q: [0.0], None)
    gen = sse.stream_agent(FakeRequest(after=10_000), "q?", ctx, {"d1": "a.txt"})
    assert await gen.__anext__() == ": ping\n\n"
    await gen.aclose()


def test_sanitize_drops_foreign_ids_and_escapes():
    out = sse.sanitize("tool_call", {"tool": "search_documents",
                       "args": {"query": "<x>", "doc_ids": ["mine", "other"]}}, {"mine"})
    assert out == {"tool": "search_documents", "args": {"query": "&lt;x&gt;", "doc_ids": ["mine"],
                                                        "top_k": 4}}
    assert sse.sanitize("tool_call", {"tool": "rm -rf", "args": {"a": 1}}, set()) == {
        "tool": "unknown", "args": {}}


def test_forwarded_strings_are_escaped_or_allow_listed():
    out = sse.sanitize("tool_result", {"tool": "<b>", "result": "x"}, set())
    assert out["tool"] == "unknown"
    out = sse.sanitize("step", {"node": "plan\r\n<i>", "n": "<x>"}, set())
    assert out == {"node": "step", "n": 0, "summary": ""}
    # stop reasons come from a fixed set; citation fields are escaped in stream_agent
    from app.services.agent.graph import AgentResult

    assert sse.stop_reason(AgentResult(answer="a", stop_reason="<weird>")) == "error"


def test_unknown_stop_reason_is_error_not_done():
    from app.services.agent.graph import AgentResult

    for sr in ("weird", "<b>", "no_such"):
        assert sse.stop_reason(AgentResult(answer="a", stop_reason=sr)) == "error"
    assert sse.stop_reason(AgentResult(answer="a", stop_reason="")) == "done"


async def test_raising_on_event_does_not_change_run(tmp_path):
    from app.services.agent.graph import run_agent
    from app.services.agent.tools import ToolContext

    ctx = ToolContext("alice", [], None, None, None, None, lambda q: [0.0], None)

    def boom(name, payload):
        raise RuntimeError("hook")

    llm.mock_script(J(route="direct", answer="hi"))
    plain = await run_agent("hello", ctx, verify=False)
    llm.mock_script(J(route="direct", answer="hi"))
    hooked = await run_agent("hello", ctx, verify=False, on_event=boom)
    assert (hooked.answer, hooked.steps, hooked.stop_reason, hooked.llm_calls) == (
        plain.answer, plain.steps, plain.stop_reason, plain.llm_calls)


async def test_cancel_signal_stops_multicall_summarize_in_worker_thread(monkeypatch):
    import threading

    from app.services import summarization
    from app.services.agent.tools import SummarizeArgs, _summarize

    calls = {"n": 0}
    cancel = threading.Event()
    real = llm.complete

    async def counting(messages, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            cancel.set()  # "client disconnects" during the second call
        return await real(messages, **kw)

    monkeypatch.setattr(llm, "complete", counting)
    monkeypatch.setattr(summarization, "make_batches", lambda secs, n: ["a", "b", "c", "d", "e"])
    monkeypatch.setenv("SUMMARY_MAX_BATCHES", "10")
    from app.core import config

    config.get_settings.cache_clear()
    summarization.clear_cache()
    llm.mock_script(*["partial"] * 20)

    class Doc:
        status, filename, doc_id = "ready", "a.txt", "d1"

    class Reg:
        def get(self, o, d):
            return Doc()

    class Par:
        def list_doc_parents(self, o, d):
            class P:
                text = "x"
            return [P()]

    from app.services.agent.tools import ToolContext

    b = llm.Budget(10_000, 10**6, 10.0)
    b.cancel = cancel
    ctx = ToolContext("alice", None, Reg(), None, Par(), None, lambda q: [0.0], None, budget=b)
    out = await asyncio.to_thread(_summarize, ctx, SummarizeArgs(doc_id="d1"))
    assert calls["n"] == 2  # nothing started after the cancel signal
    assert "partial" in out

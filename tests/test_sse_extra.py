"""T-019 extra SSE tests: framing/injection, ids, ordering, scoping, cancel, concurrency (mock LLM only)."""

import asyncio
import json

import pytest
from app.api import sse
from app.core import llm
from app.main import app
from app.services.agent.graph import AgentResult
from app.services.agent.tools import ToolContext
from fastapi.testclient import TestClient

A = {"X-User-Id": "alice"}
B = {"X-User-Id": "bob"}
EVIL = 'x\r\n\ndata: {"seq":99}\n\nevent: final\ndata: {}\r\n<script>&'


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


def doc(c, headers=A, text=b"The sky colour is blue.", name="a.txt"):
    r = c.post("/v1/documents", files=[("files", (name, text))], headers=headers)
    return r.json()["results"][0]["doc_id"]


def strict_frames(text: str):
    """Every non-comment block must be exactly `event:` + one-line `data:` JSON."""
    assert text.endswith("\n\n")
    out = []
    for block in text.split("\n\n")[:-1]:
        if block.startswith(":"):
            assert "\n" not in block
            continue
        lines = block.split("\n")
        assert len(lines) == 2, block
        assert lines[0].startswith("event: ") and lines[1].startswith("data: ")
        assert "\r" not in block
        out.append((lines[0][7:], json.loads(lines[1][6:])))
    return out


async def collect(gen):
    return "".join([f async for f in gen])


class Req:
    async def is_disconnected(self):
        return False


def ctx():
    return ToolContext("alice", ["d1"], None, None, None, None, lambda q: [0.0], None)


def fake_run(result: AgentResult, events=()):
    async def run(question, c, run_id=None, on_event=None, cancel=None, **kw):
        for n, p in events:
            on_event(n, p)
        await asyncio.sleep(0)
        return result

    return run


# ---- framing / injection -------------------------------------------------------------


async def test_injection_in_answer_filename_span_cannot_forge_frames(monkeypatch):
    res = AgentResult(
        answer=EVIL,
        citations=[{"doc_id": "d1", "span": EVIL}],
        steps=1,
    )
    monkeypatch.setattr(sse, "run_agent", fake_run(res))
    text = await collect(sse.stream_agent(Req(), "q", ctx(), {"d1": EVIL}))
    ev = strict_frames(text)
    names = [n for n, _ in ev]
    assert names.count("final") == 1 and names[-1] == "final"
    assert all(p["seq"] != 99 for _, p in ev)
    assert "<script>" not in text and "&lt;script&gt;" in text
    fin = ev[-1][1]
    assert "<" not in json.dumps(fin).replace("&lt;", "")
    toks = "".join(p["text"] for n, p in ev if n == "token")
    assert toks == fin["answer"]
    assert [p["seq"] for _, p in ev] == list(range(1, len(ev) + 1))


async def test_injection_in_tool_args_and_events(monkeypatch):
    events = [
        ("tool_call", {"tool": "search_documents", "args": {"query": EVIL}}),
        ("tool_result", {"tool": EVIL, "result": '<tool_result status="ok">\n' + EVIL + "\n</tool_result>"}),
    ]
    monkeypatch.setattr(sse, "run_agent", fake_run(AgentResult(answer="ok"), events))
    text = await collect(sse.stream_agent(Req(), "q", ctx(), {"d1": "a.txt"}))
    ev = strict_frames(text)
    assert "<script>" not in text
    tr = next(p for n, p in ev if n == "tool_result")
    assert tr["tool"] == "unknown" and set(tr) == {"run_id", "seq", "tool", "ok", "n_items"}
    assert "script" not in json.dumps(tr)


def test_step_node_is_allow_listed():
    out = sse.sanitize("step", {"node": EVIL, "n": 1}, set())
    assert "<script>" not in json.dumps(out)
    assert out["summary"] == ""


def test_unknown_tool_and_foreign_ids_in_args():
    out = sse.sanitize("tool_call", {"tool": "nope", "args": {"doc_id": "other"}}, {"mine"})
    assert out == {"tool": "unknown", "args": {}}
    out = sse.sanitize("tool_call", {"tool": "summarize_document", "args": {"doc_id": "other"}}, {"mine"})
    assert "other" not in json.dumps(out)
    out = sse.sanitize("tool_result", {"tool": "search_documents", "result": "SECRET doc text"}, {"mine"})
    assert set(out) == {"tool", "ok", "n_items"} and "SECRET" not in json.dumps(out)
    assert out["ok"] is False


def test_frame_one_line_data():
    f = sse.frame("token", {"text": "a\nb\r\nevent: x\n\ndata: y "})
    assert f.count("\n") == 3 and f.endswith("\n\n") and "\r" not in f


# ---- ids / ordering ------------------------------------------------------------------


async def test_run_id_differs_across_runs_and_seq_from_one(monkeypatch):
    monkeypatch.setattr(sse, "run_agent", fake_run(AgentResult(answer="hi there")))
    runs = []
    for _ in range(3):
        runs.append(strict_frames(await collect(sse.stream_agent(Req(), "q", ctx(), {"d1": "a"}))))
    ids = set()
    for ev in runs:
        assert [p["seq"] for _, p in ev] == list(range(1, len(ev) + 1))
        r = {p["run_id"] for _, p in ev}
        assert len(r) == 1
        ids |= r
    assert len(ids) == 3


async def test_concurrent_streams_do_not_share_state(monkeypatch):
    async def run(question, c, run_id=None, on_event=None, cancel=None, **kw):
        on_event("step", {"node": "router", "n": 1})
        await asyncio.sleep(0.01)
        return AgentResult(answer=f"answer for {question}")

    monkeypatch.setattr(sse, "run_agent", run)
    texts = await asyncio.gather(*[
        collect(sse.stream_agent(Req(), f"Q{i}", ctx(), {"d1": "a"})) for i in range(5)
    ])
    ids = set()
    for i, t in enumerate(texts):
        ev = strict_frames(t)
        assert [p["seq"] for _, p in ev] == list(range(1, len(ev) + 1))
        assert ev[-1][1]["answer"] == f"answer for Q{i}"
        ids |= {p["run_id"] for _, p in ev}
    assert len(ids) == 5


def test_full_event_order_tool_run_and_direct_reply(client):
    d = doc(client)
    llm.mock_script(ROUTE, SEARCH, FINAL, YES, f"Blue [doc_id={d}]", GOOD)
    r = client.post("/v1/query", json={"question": "sky colour?", "stream": True}, headers=A)
    names = [n for n, _ in strict_frames(r.text)]
    order = ["step", "tool_call", "tool_result", "token", "citations", "final"]
    pos = [names.index(n) for n in order]
    assert pos == sorted(pos) and names[0] == "step"
    # direct reply: router answers without tools
    llm.mock_script(J(route="direct", answer="Hello!"))
    r = client.post("/v1/query", json={"question": "hi", "stream": True}, headers=A)
    ev = strict_frames(r.text)
    names = [n for n, _ in ev]
    assert "tool_call" not in names and names[-2:] == ["citations", "final"]
    assert names[0] == "step" and "token" in names
    assert ev[-1][1]["stopped_reason"] in ("done", "abstained")


async def test_stopped_reasons_max_tokens_and_max_usd(monkeypatch):
    from app.core.config import get_settings

    cap = get_settings().agent_max_usd
    for usd, want in ((cap + 1, "max_usd"), (0.0, "max_tokens")):
        res = AgentResult(answer="partial", stop_reason="budget", usd=usd)
        monkeypatch.setattr(sse, "run_agent", fake_run(res))
        ev = strict_frames(await collect(sse.stream_agent(Req(), "q", ctx(), {"d1": "a"})))
        assert ev[-1][0] == "final" and ev[-1][1]["stopped_reason"] == want
    res = AgentResult(answer="", stop_reason="max_steps")
    monkeypatch.setattr(sse, "run_agent", fake_run(res))
    ev = strict_frames(await collect(sse.stream_agent(Req(), "q", ctx(), {"d1": "a"})))
    assert ev[-1][1]["stopped_reason"] == "max_steps"


async def test_unhandled_exception_fixed_strings_no_leak(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("/etc/secret/path.py exploded")

    monkeypatch.setattr(sse, "run_agent", boom)
    text = await collect(sse.stream_agent(Req(), "q", ctx(), {"d1": "a"}))
    ev = strict_frames(text)
    assert [n for n, _ in ev] == ["error", "final"]
    assert "secret" not in text and "exploded" not in text and "Traceback" not in text
    assert ev[0][1]["code"] == "internal" and ev[1][1]["stopped_reason"] == "error"


# ---- auth / validation / scoping -----------------------------------------------------


def test_auth_and_validation_json_before_stream(client):
    d = doc(client)
    for h in ({}, {"X-User-Id": ""}):
        r = client.post("/v1/query", json={"question": "x", "stream": True}, headers=h)
        assert r.status_code in (401, 422) and r.headers["content-type"].startswith("application/json")
    assert client.post("/v1/query", json={"question": "x", "stream": True}).status_code == 401
    for bad in ({"stream": True}, {"question": 5, "stream": True}, {"question": "x", "doc_ids": "a", "stream": True}):
        r = client.post("/v1/query", json=bad, headers=A)
        assert r.status_code == 422 and r.headers["content-type"].startswith("application/json")
    r = client.post("/v1/query", json={"question": "x", "stream": True, "doc_ids": [d]}, headers=B)
    assert r.status_code == 404 and not r.headers["content-type"].startswith("text/event-stream")


def test_other_user_never_sees_filenames(client):
    d = doc(client, name="alice-private-plan.txt")
    bob_doc = doc(client, headers=B, text=b"Bob's sky is grey.", name="bob.txt")
    llm.mock_script(ROUTE, SEARCH, FINAL, YES, f"Grey [doc_id={bob_doc}]", GOOD)
    r = client.post("/v1/query", json={"question": "sky?", "stream": True}, headers=B)
    assert r.status_code == 200
    assert "alice-private-plan" not in r.text and d not in r.text
    r = client.post("/v1/query", json={"question": "sky?", "stream": True, "doc_ids": [d]}, headers=B)
    assert r.status_code == 404 and "alice-private-plan" not in r.text


async def test_foreign_citation_dropped(monkeypatch):
    res = AgentResult(answer="a", citations=[{"doc_id": "other", "span": "s"}, {"doc_id": "d1", "span": "t"}])
    monkeypatch.setattr(sse, "run_agent", fake_run(res))
    ev = strict_frames(await collect(sse.stream_agent(Req(), "q", ctx(), {"d1": "a.txt"})))
    cites = ev[-1][1]["citations"]
    assert [c["doc_id"] for c in cites] == ["d1"]


async def test_no_ready_docs_abstains_without_running_agent(monkeypatch):
    called = []

    async def run(*a, **k):
        called.append(1)
        return AgentResult(answer="x")

    monkeypatch.setattr(sse, "run_agent", run)
    ev = strict_frames(await collect(sse.stream_agent(Req(), "q", ctx(), {})))
    assert not called
    assert [n for n, _ in ev] == ["token", "citations", "final"]
    assert ev[-1][1]["stopped_reason"] == "abstained" and ev[-1][1]["abstained"] is True
    assert [p["seq"] for _, p in ev] == [1, 2, 3]


# ---- cancel / heartbeat --------------------------------------------------------------


async def test_generator_close_cancels_task_no_leak(monkeypatch):
    calls = {"n": 0}

    async def slow(messages, **kw):
        calls["n"] += 1
        await asyncio.sleep(30)

    monkeypatch.setattr(llm, "complete", slow)
    monkeypatch.setattr(sse, "POLL_S", 0.01)
    me = asyncio.current_task()
    before = {t for t in asyncio.all_tasks() if t is not me}
    gen = sse.stream_agent(Req(), "q?", ctx(), {"d1": "a.txt"})
    nxt = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0.1)
    nxt.cancel()  # client went away mid-wait
    with pytest.raises(asyncio.CancelledError):
        await nxt
    await gen.aclose()
    await asyncio.sleep(0.05)
    n = calls["n"]
    await asyncio.sleep(0.1)
    assert calls["n"] == n
    leaked = {t for t in asyncio.all_tasks() if t is not me and not t.done()} - before
    assert not leaked


async def test_heartbeat_is_comment_and_consumes_no_seq(monkeypatch):
    async def run(question, c, run_id=None, on_event=None, cancel=None, **kw):
        await asyncio.sleep(0.15)
        return AgentResult(answer="done")

    monkeypatch.setattr(sse, "run_agent", run)
    monkeypatch.setattr(sse, "POLL_S", 0.01)
    monkeypatch.setattr(sse, "HEARTBEAT_S", 0.03)
    frames = [f async for f in sse.stream_agent(Req(), "q", ctx(), {"d1": "a"})]
    pings = [f for f in frames if f.startswith(":")]
    assert pings and all(p == ": ping\n\n" for p in pings)
    ev = strict_frames("".join(frames))
    assert [p["seq"] for _, p in ev] == list(range(1, len(ev) + 1))

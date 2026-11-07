import json
import logging
import os
import socket
import subprocess
import sys

import pytest
from app.core import llm
from app.core.llm import Budget, BudgetExceeded, LLMValidationError, complete
from pydantic import BaseModel

SP, SR = "SENTINEL-PROMPT-1212", "SENTINEL-REPLY-3434"
MSG = [{"role": "user", "content": SP}]


class Out(BaseModel):
    answer: str
    n: int


def _blob(caplog) -> str:
    return " ".join(
        r.getMessage() + json.dumps(r.__dict__, default=str) for r in caplog.records
    )


async def test_mock_same_prompt_same_reply_diff_prompt_differs():
    a = await complete(MSG, model_role="fast")
    b = await complete(MSG, model_role="agent")
    c = await complete([{"role": "user", "content": "other"}], model_role="fast")
    assert a.text == b.text != c.text


def test_mock_deterministic_across_processes():
    code = (
        "import asyncio;from app.core.llm import complete;"
        "print(asyncio.run(complete([{'role':'user','content':'hello'}],"
        "model_role='fast')).text)"
    )
    env = {**os.environ, "LLM_PROVIDER": "mock"}
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={**env, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2")
    }
    assert len(outs) == 1 and outs.pop().startswith("mock-reply-")


async def test_script_order_exhaustion_falls_back_and_reset():
    llm.mock_script("one", "two")
    assert [(await complete(MSG, model_role="fast")).text for _ in range(2)] == [
        "one",
        "two",
    ]
    assert (await complete(MSG, model_role="fast")).text.startswith("mock-reply-")
    llm.mock_script("x")
    llm.mock_script()
    assert (await complete(MSG, model_role="fast")).text.startswith("mock-reply-")


async def test_mock_opens_no_sockets(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    r = await complete(MSG, model_role="fast", schema=Out)
    assert isinstance(r.parsed, Out)


def test_block_rejects_remote_hosts():
    with pytest.raises(RuntimeError, match="blocked"):
        socket.getaddrinfo("api.anthropic.com", 443)
    s = socket.socket()
    try:
        with pytest.raises(RuntimeError, match="blocked"):
            s.connect(("93.184.216.34", 443))
    finally:
        s.close()


async def test_budget_tokens_cap_raises_and_call_logged(caplog):
    caplog.set_level(logging.INFO)
    b = Budget(max_steps=10, max_tokens=1, max_usd=10)
    llm.mock_script("a reply that is long enough")
    with pytest.raises(BudgetExceeded):
        await complete(MSG, model_role="fast", budget=b)
    assert [r.getMessage() for r in caplog.records].count("llm call") == 1
    with pytest.raises(BudgetExceeded):  # now pre-check blocks
        await complete(MSG, model_role="fast", budget=b)
    assert [r.getMessage() for r in caplog.records].count("llm call") == 1


async def test_budget_usd_cap_crossing_call_logged(caplog):
    caplog.set_level(logging.INFO)
    b = Budget(max_steps=10, max_tokens=10**9, max_usd=0.0001)
    llm.mock_script("x" * 40000)
    with pytest.raises(BudgetExceeded, match="usd"):
        await complete(MSG, model_role="agent", budget=b)
    recs = [r for r in caplog.records if r.getMessage() == "llm call"]
    assert len(recs) == 1 and recs[0].usd > 0.0001


async def test_budget_steps_crossing_and_precheck():
    b = Budget(max_steps=2, max_tokens=10**9, max_usd=10)
    await complete(MSG, model_role="fast", budget=b)
    await complete(MSG, model_role="fast", budget=b)
    with pytest.raises(BudgetExceeded, match="steps"):
        await complete(MSG, model_role="fast", budget=b)
    assert b.steps == 2


async def test_mock_logs_never_contain_prompt_or_reply_incl_errors(caplog, monkeypatch):
    async def _nosleep(_):
        pass

    monkeypatch.setattr(llm, "_sleep", _nosleep)
    caplog.set_level(logging.DEBUG)
    await complete(MSG, model_role="fast")
    llm.mock_script(SR)
    await complete(MSG, model_role="fast")
    llm.mock_script(SR + " bad", SR + " bad2")
    with pytest.raises(LLMValidationError):
        await complete(MSG, model_role="fast", schema=Out)
    llm.mock_script(llm.LLMTransportError(503), llm.LLMTransportError(400))
    with pytest.raises(llm.LLMTransportError):
        await complete(MSG, model_role="fast")
    assert SP not in _blob(caplog) and SR not in _blob(caplog)
    lines = [r for r in caplog.records if r.getMessage() == "llm call"]
    assert lines and all(
        hasattr(r, "role") and hasattr(r, "usd") and hasattr(r, "latency_ms")
        for r in lines
    )

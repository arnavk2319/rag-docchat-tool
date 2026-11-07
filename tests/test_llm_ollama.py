"""Ollama provider against httpx.MockTransport (no network)."""

import json
import logging

import httpx
import pytest
from app.core import llm
from app.core.llm import (
    Budget,
    BudgetExceeded,
    LLMTransportError,
    LLMValidationError,
    complete,
    validate_provider,
)
from pydantic import BaseModel

SP, SR = "SENTINEL-PROMPT-1", "SENTINEL-REPLY-2"
MSG = [{"role": "user", "content": SP}]


class Out(BaseModel):
    answer: str
    n: int


def reply(content: str, pin: int = 11, pout: int = 7) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "message": {"role": "assistant", "content": content},
            "prompt_eval_count": pin,
            "eval_count": pout,
        },
    )


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test:11434")
    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(llm, "_sleep", fake_sleep)
    state = {"queue": [], "reqs": []}

    def handler(req: httpx.Request):
        state["reqs"].append(req)
        item = state["queue"].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(llm, "_ollama_transport", httpx.MockTransport(handler))
    state["sleeps"] = sleeps
    return state


async def test_success_usage_and_request(srv, caplog):
    srv["queue"] = [reply(SR)]
    with caplog.at_level(logging.INFO, logger="app.llm"):
        r = await complete(MSG, model_role="fast")
    assert (r.text, r.tokens_in, r.tokens_out, r.usd) == (SR, 11, 7, 0.0)
    req = srv["reqs"][0]
    assert str(req.url) == "http://ollama.test:11434/api/chat"
    body = json.loads(req.content)
    assert body["stream"] is False and body["model"] == "llama3.2:3b"
    assert "format" not in body
    assert SP not in caplog.text and SR not in caplog.text


async def test_structured_format_and_parse(srv):
    srv["queue"] = [reply('{"answer": "a", "n": 2}')]
    r = await complete(MSG, model_role="agent", schema=Out)
    assert r.parsed == Out(answer="a", n=2)
    body = json.loads(srv["reqs"][0].content)
    assert body["format"] == Out.model_json_schema()


async def test_malformed_repair_then_ok_and_fail(srv):
    srv["queue"] = [reply("nope"), reply('{"answer": "a", "n": 1}')]
    r = await complete(MSG, model_role="fast", schema=Out)
    assert r.parsed is not None and len(srv["reqs"]) == 2
    srv["queue"] = [reply("nope"), reply("still")]
    with pytest.raises(LLMValidationError):
        await complete(MSG, model_role="fast", schema=Out)


@pytest.mark.parametrize("code", [429, 500, 503])
async def test_retryable_status(srv, code):
    srv["queue"] = [httpx.Response(code)] * 4
    with pytest.raises(LLMTransportError) as ei:
        await complete(MSG, model_role="fast")
    assert ei.value.status == code
    assert len(srv["reqs"]) == 4 and len(srv["sleeps"]) == 3


async def test_retry_then_success(srv):
    srv["queue"] = [httpx.Response(503), reply("ok")]
    assert (await complete(MSG, model_role="fast")).text == "ok"


async def test_4xx_not_retried(srv):
    srv["queue"] = [httpx.Response(404)]
    with pytest.raises(LLMTransportError) as ei:
        await complete(MSG, model_role="fast")
    assert ei.value.status == 404 and len(srv["reqs"]) == 1 and not srv["sleeps"]


async def test_timeout_retried(srv):
    srv["queue"] = [httpx.ReadTimeout("t"), reply("ok")]
    assert (await complete(MSG, model_role="fast")).text == "ok"
    assert len(srv["sleeps"]) == 1


async def test_connection_refused_typed(srv):
    srv["queue"] = [httpx.ConnectError("refused")] * 4
    with pytest.raises(LLMTransportError) as ei:
        await complete(MSG, model_role="fast")
    assert ei.value.status is None and len(srv["reqs"]) == 4


async def test_malformed_body_is_transport_error(srv):
    srv["queue"] = [httpx.Response(200, json={"x": 1})] * 4
    with pytest.raises(LLMTransportError):
        await complete(MSG, model_role="fast")


async def test_budget_caps(srv):
    srv["queue"] = [reply("a", 50, 50), reply("b")]
    b = Budget(max_steps=5, max_tokens=60, max_usd=1)
    with pytest.raises(BudgetExceeded):
        await complete(MSG, model_role="fast", budget=b)
    with pytest.raises(BudgetExceeded):
        await complete(MSG, model_role="fast", budget=b)
    assert len(srv["reqs"]) == 1


def test_validate_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert validate_provider() == "ollama"
    llm.get_settings.cache_clear()
    monkeypatch.setenv("LLM_PROVIDER", "bogus")
    with pytest.raises(ValueError, match="bogus"):
        validate_provider()


def test_startup_rejects_unknown_provider(monkeypatch):
    from app.main import app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LLM_PROVIDER", "bogus")
    llm.get_settings.cache_clear()
    with pytest.raises(ValueError, match="anthropic, ollama, mock"), TestClient(app):
        pass


def test_startup_ok_with_mock_provider():
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200


async def test_404_names_model(srv):
    srv["queue"] = [httpx.Response(404)]
    with pytest.raises(LLMTransportError, match="ollama pull llama3.2:3b") as ei:
        await complete(MSG, model_role="fast")
    assert SP not in str(ei.value)

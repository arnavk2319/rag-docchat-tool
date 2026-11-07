"""Extra T-018 probes of the Ollama path (httpx.MockTransport, no network)."""

import json
import logging
import socket

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

SP, SR = "SENTINEL-PROMPT-X9", "SENTINEL-REPLY-Y8"
MSG = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": SP},
    {"role": "assistant", "content": "earlier"},
    {"role": "user", "content": "again"},
]


class Out(BaseModel):
    answer: str
    n: int


def ok(content: object = "hi", **extra: object) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}, **extra})


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test:11434")
    state: dict = {"queue": [], "reqs": [], "sleeps": []}

    async def fake_sleep(d):
        state["sleeps"].append(d)

    def handler(req: httpx.Request):
        state["reqs"].append(req)
        item = state["queue"].pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(llm, "_sleep", fake_sleep)
    monkeypatch.setattr(llm, "_ollama_transport", httpx.MockTransport(handler))
    return state


@pytest.mark.parametrize(
    "base", ["http://ollama.test:11434", "http://ollama.test:11434/"]
)
async def test_url_join_with_and_without_trailing_slash(srv, monkeypatch, base):
    monkeypatch.setenv("OLLAMA_BASE_URL", base)
    llm.get_settings.cache_clear()
    srv["queue"] = [ok()]
    await complete(MSG, model_role="fast")
    assert str(srv["reqs"][0].url) == "http://ollama.test:11434/api/chat"


async def test_body_shape_roles_models_options(srv, monkeypatch):
    monkeypatch.setenv("OLLAMA_AGENT_MODEL", "agent-x")
    monkeypatch.setenv("OLLAMA_FAST_MODEL", "fast-y")
    llm.get_settings.cache_clear()
    srv["queue"] = [ok(), ok()]
    await complete(MSG, model_role="agent", max_tokens=77)
    await complete(MSG, model_role="fast")
    b1, b2 = (json.loads(r.content) for r in srv["reqs"])
    assert b1["model"] == "agent-x" and b2["model"] == "fast-y"
    assert b1["messages"] == MSG
    assert [m["role"] for m in b1["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert b1["stream"] is False and "format" not in b1
    assert b1["options"]["num_predict"] == 77
    assert srv["reqs"][0].method == "POST"


async def test_default_models_per_role(srv):
    srv["queue"] = [ok(), ok()]
    await complete(MSG, model_role="agent")
    await complete(MSG, model_role="fast")
    ms = [json.loads(r.content)["model"] for r in srv["reqs"]]
    assert ms == ["llama3.1:8b", "llama3.2:3b"]


async def test_timeout_passed_to_httpx(srv):
    srv["queue"] = [ok()]
    await complete(MSG, model_role="fast", timeout=3.5)
    assert srv["reqs"][0].extensions["timeout"]["read"] == 3.5


async def test_nonpositive_timeout_rejected(srv):
    with pytest.raises(ValueError):
        await complete(MSG, model_role="fast", timeout=0)
    assert not srv["reqs"]


async def test_missing_counters_default_zero(srv):
    srv["queue"] = [ok("hello")]
    r = await complete(MSG, model_role="fast")
    assert (r.tokens_in, r.tokens_out, r.usd) == (0, 0, 0.0)


async def test_null_counters_treated_as_zero(srv):
    srv["queue"] = [ok("hello", prompt_eval_count=None, eval_count=None)]
    r = await complete(MSG, model_role="fast")
    assert (r.tokens_in, r.tokens_out, r.usd) == (0, 0, 0.0)


async def test_usd_zero_and_latency_logged(srv, caplog):
    srv["queue"] = [ok("x", prompt_eval_count=1000, eval_count=1000)]
    with caplog.at_level(logging.INFO, logger="app.llm"):
        r = await complete(MSG, model_role="agent")
    assert r.usd == 0 and r.latency_s >= 0
    rec = next(x for x in caplog.records if x.getMessage() == "llm call")
    assert rec.usd == 0 and rec.provider == "ollama"
    assert rec.tokens_in == 1000 and rec.tokens_out == 1000
    assert isinstance(rec.latency_ms, int) and rec.latency_ms >= 0


async def test_empty_content_returned_as_empty_text(srv):
    srv["queue"] = [ok("")]
    assert (await complete(MSG, model_role="fast")).text == ""


async def test_null_content_not_stringified(srv):
    srv["queue"] = [ok(None)] * 4
    try:
        r = await complete(MSG, model_role="fast")
    except LLMTransportError:
        return
    assert r.text != "None"


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"message": {}}),
        httpx.Response(200, json={"message": "str"}),
        httpx.Response(200, json={}),
    ],
)
async def test_malformed_bodies_typed_502_retried(srv, resp):
    srv["queue"] = [resp] * 4
    with pytest.raises(LLMTransportError) as ei:
        await complete(MSG, model_role="fast")
    assert ei.value.status == 502 and len(srv["reqs"]) == 4


async def test_empty_content_with_schema_fails_validation(srv):
    srv["queue"] = [ok(""), ok("")]
    with pytest.raises(LLMValidationError):
        await complete(MSG, model_role="fast", schema=Out)


async def test_backoff_jitter_bounded(srv):
    srv["queue"] = [httpx.Response(500)] * 4
    with pytest.raises(LLMTransportError):
        await complete(MSG, model_role="fast")
    for i, d in enumerate(srv["sleeps"]):
        cap = min(8.0, 0.5 * 2**i)
        assert cap * 0.5 <= d <= cap
    assert len(srv["sleeps"]) == 3


@pytest.mark.parametrize("code", [400, 401, 404, 422])
async def test_4xx_no_retry_typed(srv, code):
    srv["queue"] = [httpx.Response(code, json={"error": "model not found"})]
    with pytest.raises(LLMTransportError) as ei:
        await complete(MSG, model_role="fast")
    assert ei.value.status == code and not ei.value.retryable
    assert len(srv["reqs"]) == 1 and srv["sleeps"] == []


async def test_connect_error_retried_then_typed(srv):
    srv["queue"] = [httpx.ConnectError("refused")] * 4
    with pytest.raises(LLMTransportError) as ei:
        await complete(MSG, model_role="fast")
    assert ei.value.status is None and ei.value.retryable
    assert len(srv["sleeps"]) == 3


async def test_connect_error_then_recovers(srv):
    srv["queue"] = [httpx.ConnectError("x"), httpx.ConnectTimeout("y"), ok("fine")]
    assert (await complete(MSG, model_role="fast")).text == "fine"
    assert len(srv["sleeps"]) == 2


async def test_retries_respect_configured_max(srv, monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "1")
    llm.get_settings.cache_clear()
    srv["queue"] = [httpx.Response(503)] * 2
    with pytest.raises(LLMTransportError):
        await complete(MSG, model_role="fast")
    assert len(srv["reqs"]) == 2 and len(srv["sleeps"]) == 1


async def test_repair_retry_message_shape_and_budget(srv):
    srv["queue"] = [
        ok("bad", prompt_eval_count=5, eval_count=5),
        ok('{"answer":"a","n":1}', prompt_eval_count=5, eval_count=5),
    ]
    b = Budget(max_steps=5, max_tokens=1000, max_usd=1)
    r = await complete(MSG, model_role="fast", schema=Out, budget=b)
    assert r.parsed == Out(answer="a", n=1)
    assert b.steps == 2 and b.tokens == 20 and b.usd == 0
    second = json.loads(srv["reqs"][1].content)
    assert [m["role"] for m in second["messages"][-2:]] == ["assistant", "user"]
    assert second["messages"][-2]["content"] == "bad"
    assert second["format"] == Out.model_json_schema()


async def test_repair_blocked_by_step_budget(srv):
    srv["queue"] = [ok("bad"), ok('{"answer":"a","n":1}')]
    b = Budget(max_steps=1, max_tokens=1000, max_usd=1)
    with pytest.raises(BudgetExceeded):
        await complete(MSG, model_role="fast", schema=Out, budget=b)
    assert len(srv["reqs"]) == 1


async def test_step_cap_on_charge(srv):
    srv["queue"] = [ok(), ok()]
    b = Budget(max_steps=1, max_tokens=1000, max_usd=1)
    await complete(MSG, model_role="fast", budget=b)
    with pytest.raises(BudgetExceeded):
        await complete(MSG, model_role="fast", budget=b)
    assert len(srv["reqs"]) == 1


async def test_retries_do_not_charge_budget(srv):
    srv["queue"] = [httpx.Response(503), ok("a", prompt_eval_count=3, eval_count=4)]
    b = Budget(max_steps=5, max_tokens=1000, max_usd=1)
    await complete(MSG, model_role="fast", budget=b)
    assert b.steps == 1 and b.tokens == 7


async def test_logs_never_contain_prompt_or_reply_on_errors(srv, caplog):
    cases = [
        [ok(SR), ok(SR)],  # schema fail after repair
        [httpx.Response(500, text=SR)] * 4,
        [httpx.Response(404, text=SR)],
        [httpx.ConnectError(SP)] * 4,
        [httpx.Response(200, text=SR)] * 4,  # malformed
    ]
    with caplog.at_level(logging.DEBUG):
        for q in cases:
            srv["queue"] = list(q)
            with pytest.raises(llm.LLMError):
                await complete(MSG, model_role="fast", schema=Out)
    assert caplog.records
    assert SP not in caplog.text and SR not in caplog.text


async def test_logs_clean_on_success_with_schema(srv, caplog):
    srv["queue"] = [ok(SR), ok('{"answer":"a","n":1}')]
    with caplog.at_level(logging.DEBUG):
        await complete(MSG, model_role="fast", schema=Out)
    assert SP not in caplog.text and SR not in caplog.text


def test_network_still_blocked():
    with pytest.raises(RuntimeError, match="network blocked"):
        socket.getaddrinfo("example.com", 80)


async def test_ollama_default_transport_is_none():
    assert llm._ollama_transport is None


async def test_dispatch_mock_unaffected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    llm.get_settings.cache_clear()
    called = []

    async def boom(*a, **k):
        called.append(1)
        raise AssertionError

    monkeypatch.setattr(llm, "_call_ollama", boom)
    monkeypatch.setattr(llm, "_call_hosted", boom)
    r = await complete(MSG, model_role="fast")
    assert r.text.startswith("mock-reply-") and not called


async def test_dispatch_anthropic_uses_hosted_and_hosted_models(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    llm.get_settings.cache_clear()
    seen = {}

    async def fake_hosted(s, messages, model, max_tokens, timeout):
        seen["model"] = model
        return "t", 1, 1

    async def boom(*a, **k):
        raise AssertionError("ollama must not be called")

    monkeypatch.setattr(llm, "_call_hosted", fake_hosted)
    monkeypatch.setattr(llm, "_call_ollama", boom)
    r = await complete(MSG, model_role="fast")
    s = llm.get_settings()
    assert seen["model"] == s.fast_model == r.model


async def test_dispatch_ollama_not_hosted(srv, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("hosted must not be called")

    monkeypatch.setattr(llm, "_call_hosted", boom)
    srv["queue"] = [ok()]
    await complete(MSG, model_role="fast")


@pytest.mark.parametrize("p", ["anthropic", "ollama", "mock"])
def test_validate_provider_accepts(monkeypatch, p):
    monkeypatch.setenv("LLM_PROVIDER", p)
    llm.get_settings.cache_clear()
    assert validate_provider() == p


@pytest.mark.parametrize("p", ["Ollama", "openai", "", " mock"])
def test_validate_provider_rejects(monkeypatch, p):
    monkeypatch.setenv("LLM_PROVIDER", p)
    llm.get_settings.cache_clear()
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        validate_provider()


async def test_complete_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bogus")
    llm.get_settings.cache_clear()
    with pytest.raises(ValueError, match="bogus"):
        await complete(MSG, model_role="fast")


def test_startup_default_provider_ok(monkeypatch):
    from app.main import app
    from fastapi.testclient import TestClient

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    llm.get_settings.cache_clear()
    assert validate_provider() == "mock"
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200


def test_startup_unknown_provider_message_names_value(monkeypatch):
    from app.main import app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LLM_PROVIDER", "ollamaa")
    llm.get_settings.cache_clear()
    with pytest.raises(ValueError, match="ollamaa"), TestClient(app):
        pass

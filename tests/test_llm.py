import logging
import socket

import pytest
from app.core import llm
from app.core.llm import (
    Budget,
    BudgetExceeded,
    LLMTransportError,
    LLMValidationError,
    complete,
)
from pydantic import BaseModel

MSG = [{"role": "user", "content": "hello"}]


class Out(BaseModel):
    answer: str
    n: int


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []

    async def fake(s):
        calls.append(s)

    monkeypatch.setattr(llm, "_sleep", fake)
    return calls


async def test_mock_deterministic():
    a = await complete(MSG, model_role="fast")
    b = await complete(MSG, model_role="fast")
    c = await complete([{"role": "user", "content": "other"}], model_role="fast")
    assert a.text == b.text != c.text
    assert a.model == "claude-haiku-5-5"


async def test_roles_map_to_models():
    assert (await complete(MSG, model_role="agent")).model == "claude-sonnet-5-5"


async def test_scripted_queue_then_fallback():
    llm.mock_script("one", "two")
    assert (await complete(MSG, model_role="fast")).text == "one"
    assert (await complete(MSG, model_role="fast")).text == "two"
    assert (await complete(MSG, model_role="fast")).text.startswith("mock-reply-")


@pytest.mark.parametrize("status", [429, 500, 503, None])
async def test_retry_then_success(sleeps, status):
    llm.mock_script(LLMTransportError(status), LLMTransportError(status), "ok")
    assert (await complete(MSG, model_role="fast")).text == "ok"
    assert len(sleeps) == 2 and sleeps[1] > 0 and sleeps[0] <= 0.5


async def test_retry_gives_up_after_3(sleeps):
    llm.mock_script(*[LLMTransportError(500)] * 4)
    with pytest.raises(LLMTransportError):
        await complete(MSG, model_role="fast")
    assert len(sleeps) == 3


async def test_no_retry_on_4xx(sleeps):
    llm.mock_script(LLMTransportError(400), "never")
    with pytest.raises(LLMTransportError):
        await complete(MSG, model_role="fast")
    assert sleeps == []


async def test_schema_ok_and_default_mock_valid():
    r = await complete(MSG, model_role="fast", schema=Out)
    assert isinstance(r.parsed, Out)


async def test_schema_repair_once():
    llm.mock_script("not json", '{"answer": "a", "n": 2}')
    r = await complete(MSG, model_role="fast", schema=Out)
    assert r.parsed == Out(answer="a", n=2)


async def test_schema_fails_after_repair():
    llm.mock_script("bad", '{"answer": 1}', "never")
    with pytest.raises(LLMValidationError):
        await complete(MSG, model_role="fast", schema=Out)


async def test_budget_steps():
    b = Budget(max_steps=2, max_tokens=10**6, max_usd=10)
    await complete(MSG, model_role="fast", budget=b)
    await complete(MSG, model_role="fast", budget=b)
    with pytest.raises(BudgetExceeded, match="steps"):
        await complete(MSG, model_role="fast", budget=b)


async def test_budget_tokens():
    b = Budget(max_steps=10, max_tokens=5, max_usd=10)
    with pytest.raises(BudgetExceeded, match="tokens"):
        await complete(
            [{"role": "user", "content": "x" * 400}], model_role="fast", budget=b
        )


async def test_budget_usd():
    b = Budget(max_steps=10, max_tokens=10**9, max_usd=0.0001)
    with pytest.raises(BudgetExceeded, match="usd"):
        await complete(
            [{"role": "user", "content": "x" * 4000}], model_role="agent", budget=b
        )


async def test_cost_computed():
    r = await complete([{"role": "user", "content": "x" * 4000}], model_role="agent")
    assert r.tokens_in == 1000 and r.usd == pytest.approx(
        (1000 * 3.0 + r.tokens_out * 15.0) / 1e6
    )


async def test_log_has_metrics_not_text(caplog):
    sentinel = "SENTINEL-SECRET-PROMPT-42"
    llm.mock_script("RESPONSE-SENTINEL-77")
    with caplog.at_level(logging.DEBUG):
        await complete(
            [{"role": "user", "content": sentinel}], model_role="fast", run_id="r1"
        )
    from app.core.logging import JsonFormatter

    lines = [JsonFormatter().format(r) for r in caplog.records]
    blob = "\n".join(lines)
    assert sentinel not in blob and "RESPONSE-SENTINEL-77" not in blob
    rec = next(r for r in caplog.records if r.getMessage() == "llm call")
    assert (
        rec.role == "fast"
        and rec.run_id == "r1"
        and rec.tokens_in > 0
        and rec.latency_ms >= 0
    )
    assert hasattr(rec, "usd") and rec.model == "claude-haiku-5-5"


async def test_unknown_and_ollama_providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    with pytest.raises(NotImplementedError, match="T-018"):
        await complete(MSG, model_role="fast")
    monkeypatch.setenv("LLM_PROVIDER", "bogus")
    llm.get_settings.cache_clear()
    with pytest.raises(ValueError):
        await complete(MSG, model_role="fast")


def test_network_blocked():
    with pytest.raises(Exception, match="blocked"):
        socket.create_connection(("api.anthropic.com", 443), timeout=1)


@pytest.mark.parametrize("bad", [0, -1, 0.0])
async def test_explicit_bad_timeout_raises(bad):
    with pytest.raises(ValueError, match="timeout"):
        await complete(MSG, model_role="fast", timeout=bad)


async def test_mock_never_reads_api_key(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("api key touched")

    monkeypatch.setattr("pydantic.SecretStr.get_secret_value", boom)
    assert (await complete(MSG, model_role="fast")).text

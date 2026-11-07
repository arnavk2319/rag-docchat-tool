"""Hosted-provider path against a fake SDK client (no network, no key)."""

import json
import logging
from types import SimpleNamespace

import anthropic
import httpx
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

SENTINEL_P = "SENTINEL-PROMPT-7731"
SENTINEL_R = "SENTINEL-REPLY-9915"
MSG = [{"role": "user", "content": SENTINEL_P}]
REQ = httpx.Request("POST", "https://example.invalid/v1/messages")


class Out(BaseModel):
    answer: str
    n: int


def status_err(code: int) -> anthropic.APIStatusError:
    resp = httpx.Response(code, request=REQ)
    return anthropic.APIStatusError("boom", response=resp, body=None)


def ok(text: str, tin: int = 1000, tout: int = 500):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=tin, output_tokens=tout),
    )


@pytest.fixture
def fake(monkeypatch):
    """Queue of responses/exceptions served by a fake AsyncAnthropic."""
    state = SimpleNamespace(queue=[], calls=[])

    class Client:
        def __init__(self, **kw):
            state.init = kw
            self.messages = self

        async def create(self, **kw):
            state.calls.append(kw)
            item = state.queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(llm.anthropic, "AsyncAnthropic", Client)
    return state


@pytest.fixture
def sleeps(monkeypatch):
    calls: list[float] = []

    async def fake_sleep(s):
        calls.append(s)

    monkeypatch.setattr(llm, "_sleep", fake_sleep)
    return calls


async def test_success_usd_from_price_table(fake):
    fake.queue = [ok("hi", 1000, 500)]
    r = await complete(MSG, model_role="agent", max_tokens=77, timeout=5)
    assert (r.text, r.tokens_in, r.tokens_out) == ("hi", 1000, 500)
    assert r.usd == pytest.approx((1000 * 3.0 + 500 * 15.0) / 1e6)
    assert fake.calls[0]["model"] == "claude-sonnet-5-5"
    assert fake.calls[0]["max_tokens"] == 77
    assert fake.init["timeout"] == 5 and fake.init["max_retries"] == 0


async def test_fast_role_uses_fast_price(fake):
    fake.queue = [ok("hi", 1_000_000, 1_000_000)]
    r = await complete(MSG, model_role="fast")
    assert r.usd == pytest.approx(1.0 + 5.0)


async def test_system_messages_split(fake):
    fake.queue = [ok("x")]
    await complete(
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}],
        model_role="fast",
    )
    c = fake.calls[0]
    assert c["system"] == "sys" and c["messages"] == [{"role": "user", "content": "u"}]


async def test_timeout_retried_with_bounded_backoff(fake, sleeps):
    t = anthropic.APITimeoutError(request=REQ)
    fake.queue = [t, t, ok("fine")]
    r = await complete(MSG, model_role="fast")
    assert r.text == "fine" and len(fake.calls) == 3
    assert len(sleeps) == 2
    assert 0.25 <= sleeps[0] <= 0.5 and 0.5 <= sleeps[1] <= 1.0
    assert all(0 < d <= 8.0 for d in sleeps)


@pytest.mark.parametrize("code", [429, 500, 503])
async def test_retryable_status_up_to_3_then_raised(fake, sleeps, code):
    fake.queue = [status_err(code) for _ in range(4)]
    with pytest.raises(LLMTransportError) as ei:
        await complete(MSG, model_role="fast")
    assert ei.value.status == code
    assert len(fake.calls) == 4 and len(sleeps) == 3
    assert all(d <= 8.0 for d in sleeps)


async def test_connection_error_retried(fake, sleeps):
    fake.queue = [anthropic.APIConnectionError(request=REQ), ok("z")]
    assert (await complete(MSG, model_role="fast")).text == "z"
    assert len(fake.calls) == 2


@pytest.mark.parametrize("code", [400, 401, 404])
async def test_4xx_not_retried(fake, sleeps, code):
    fake.queue = [status_err(code), ok("never")]
    with pytest.raises(LLMTransportError) as ei:
        await complete(MSG, model_role="fast")
    assert ei.value.status == code and len(fake.calls) == 1 and sleeps == []


async def test_valid_structured_output(fake):
    fake.queue = [ok(json.dumps({"answer": "a", "n": 2}))]
    r = await complete(MSG, model_role="fast", schema=Out)
    assert isinstance(r.parsed, Out) and r.parsed == Out(answer="a", n=2)


async def test_malformed_json_one_repair_then_ok(fake):
    fake.queue = [ok("{not json"), ok('{"answer": "a", "n": 1}')]
    r = await complete(MSG, model_role="fast", schema=Out)
    assert r.parsed == Out(answer="a", n=1) and len(fake.calls) == 2
    repair_msgs = fake.calls[1]["messages"]
    assert repair_msgs[-2]["role"] == "assistant" and repair_msgs[-1]["role"] == "user"


async def test_malformed_json_twice_raises(fake):
    fake.queue = [ok("nope"), ok("still nope"), ok('{"answer":"a","n":1}')]
    with pytest.raises(LLMValidationError):
        await complete(MSG, model_role="fast", schema=Out)
    assert len(fake.calls) == 2


async def test_repair_counts_against_budget(fake):
    fake.queue = [ok("bad"), ok("bad2")]
    b = Budget(max_steps=10, max_tokens=10**6, max_usd=10)
    with pytest.raises(LLMValidationError):
        await complete(MSG, model_role="fast", schema=Out, budget=b)
    assert b.steps == 2


async def test_repair_blocked_when_step_cap_hit(fake):
    fake.queue = [ok("bad"), ok('{"answer":"a","n":1}')]
    b = Budget(max_steps=1, max_tokens=10**6, max_usd=10)
    with pytest.raises(BudgetExceeded):
        await complete(MSG, model_role="fast", schema=Out, budget=b)
    assert len(fake.calls) == 1


async def test_hosted_logs_no_text_on_success_error_repair(fake, sleeps, caplog):
    caplog.set_level(logging.DEBUG)
    fake.queue = [
        status_err(500),
        ok(SENTINEL_R + " not json"),
        ok(json.dumps({"answer": SENTINEL_R, "n": 1})),
    ]
    await complete(MSG, model_role="agent", schema=Out, run_id="r1")
    fake.queue = [status_err(401)]
    with pytest.raises(LLMTransportError):
        await complete(MSG, model_role="agent")
    fake.queue = [ok(SENTINEL_R), ok(SENTINEL_R)]
    with pytest.raises(LLMValidationError):
        await complete(MSG, model_role="agent", schema=Out)
    blob = " ".join(
        r.getMessage() + json.dumps(r.__dict__, default=str) for r in caplog.records
    )
    assert SENTINEL_P not in blob and SENTINEL_R not in blob
    calls = [r for r in caplog.records if r.getMessage() == "llm call"]
    assert len(calls) == 4
    for r in calls:
        for f in ("role", "model", "tokens_in", "tokens_out", "usd", "latency_ms"):
            assert hasattr(r, f)
    assert any(r.repair for r in calls)

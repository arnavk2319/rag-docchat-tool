"""The only module that talks to a model provider (hosted SDK, Ollama, or mock).

complete() = budget pre-check -> provider call with retry/backoff -> cost log -> optional
schema validation with one repair retry. Logs never contain prompt or response text.
"""

import asyncio
import hashlib
import json
import logging
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal

import anthropic
import httpx
from pydantic import BaseModel, ValidationError

from .config import Settings, get_settings

log = logging.getLogger("app.llm")
Role = Literal["agent", "fast"]
Message = dict[str, str]


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    """Cap hit. If raised by Budget.charge inside complete(), `result` is the already-paid LLMResult."""

    def __init__(self, *args: object, result: "LLMResult | None" = None) -> None:
        super().__init__(*args)
        self.result = result


class LLMValidationError(LLMError):
    pass


class LLMTransportError(LLMError):
    """Provider failure. status=None means timeout/connection error (retryable)."""

    def __init__(self, status: int | None, msg: str = "") -> None:
        super().__init__(f"transport error status={status} {msg}".strip())
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status == 429 or self.status >= 500


@dataclass
class LLMResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    usd: float
    latency_s: float
    parsed: BaseModel | None = None


class Budget:
    """Per-run caps on LLM steps, tokens and USD. Raises BudgetExceeded."""

    def __init__(self, max_steps: int, max_tokens: int, max_usd: float) -> None:
        self.max_steps, self.max_tokens, self.max_usd = max_steps, max_tokens, max_usd
        self.steps = self.tokens = 0
        self.usd = 0.0
        self.cancel: threading.Event | None = None  # per-run; set => no new LLM call starts

    def check(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise BudgetExceeded("cancelled")
        if self.steps >= self.max_steps:
            raise BudgetExceeded(f"steps {self.steps}/{self.max_steps}")
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded(f"tokens {self.tokens}/{self.max_tokens}")
        if self.usd >= self.max_usd:
            raise BudgetExceeded(f"usd {self.usd:.4f}/{self.max_usd}")

    def charge(self, tokens: int, usd: float) -> None:
        self.steps += 1
        self.tokens += tokens
        self.usd += usd
        if self.tokens > self.max_tokens:
            raise BudgetExceeded(f"tokens {self.tokens}/{self.max_tokens}")
        if self.usd > self.max_usd:
            raise BudgetExceeded(f"usd {self.usd:.4f}/{self.max_usd}")
        if self.steps > self.max_steps:
            raise BudgetExceeded(f"steps {self.steps}/{self.max_steps}")


# ---- mock provider -------------------------------------------------------------------------
_script: deque[Any] = deque()


def mock_script(*items: Any) -> None:
    """Queue replies for the mock provider. str = reply text; Exception = raised; reset first."""
    _script.clear()
    _script.extend(items)


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _example(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    if "$ref" in schema:
        return _example(defs[schema["$ref"].split("/")[-1]], defs)
    if "anyOf" in schema:
        return _example(
            next((s for s in schema["anyOf"] if s.get("type") != "null"), {}), defs
        )
    t = schema.get("type")
    if t == "object":
        return {k: _example(v, defs) for k, v in schema.get("properties", {}).items()}
    return {
        "string": "mock",
        "integer": 0,
        "number": 0.0,
        "boolean": False,
        "array": [],
    }.get(t)


def _mock_reply(messages: list[Message], schema: type[BaseModel] | None) -> str:
    digest = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()[
        :12
    ]
    if schema is None:
        return f"mock-reply-{digest}"
    js = schema.model_json_schema()
    return json.dumps(_example(js, js.get("$defs", {})))


# ---- providers -------------------------------------------------------------------------------
async def _call_mock(
    messages: list[Message], model: str, schema: type[BaseModel] | None
) -> tuple[str, int, int]:
    item = _script.popleft() if _script else _mock_reply(messages, schema)
    if isinstance(item, BaseException):
        raise item
    tin = sum(_approx_tokens(m["content"]) for m in messages)
    return str(item), tin, _approx_tokens(str(item))


async def _call_hosted(
    s: Settings, messages: list[Message], model: str, max_tokens: int, timeout: float
) -> tuple[str, int, int]:
    client = anthropic.AsyncAnthropic(
        api_key=s.api_key.get_secret_value() or None, max_retries=0, timeout=timeout
    )
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [m for m in messages if m["role"] != "system"]
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=turns,  # type: ignore[arg-type]
            **({"system": system} if system else {}),
        )
    except anthropic.APIStatusError as e:
        raise LLMTransportError(e.status_code, type(e).__name__) from e
    except (anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
        raise LLMTransportError(None, type(e).__name__) from e
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return text, resp.usage.input_tokens, resp.usage.output_tokens


# Tests set this to an httpx.MockTransport; None = real network.
_ollama_transport: httpx.AsyncBaseTransport | None = None


async def _call_ollama(
    s: Settings,
    messages: list[Message],
    model: str,
    schema: type[BaseModel] | None,
    max_tokens: int,
    timeout: float,
) -> tuple[str, int, int]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if schema is not None:
        body["format"] = schema.model_json_schema()
    url = s.ollama_base_url.rstrip("/") + "/api/chat"
    try:
        async with httpx.AsyncClient(
            timeout=timeout, transport=_ollama_transport
        ) as client:
            resp = await client.post(url, json=body)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise LLMTransportError(None, type(e).__name__) from e
    if resp.status_code == 404:
        raise LLMTransportError(
            404, f"ollama model {model!r} not found; try `ollama pull {model}`"
        )
    if resp.status_code >= 400:
        raise LLMTransportError(resp.status_code, "ollama")
    try:
        data = resp.json()
        content = data["message"]["content"]
        text = "" if content is None else str(content)
        return (
            text,
            int(data.get("prompt_eval_count") or 0),
            int(data.get("eval_count") or 0),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise LLMTransportError(502, "ollama malformed response") from e


PROVIDERS = ("anthropic", "ollama", "mock")


def validate_provider(s: Settings | None = None) -> str:
    """Fail clearly on an unknown LLM_PROVIDER (call from the FastAPI lifespan)."""
    p = (s or get_settings()).llm_provider
    if p not in PROVIDERS:
        raise ValueError(
            f"unknown LLM_PROVIDER {p!r}; expected one of {', '.join(PROVIDERS)}"
        )
    return p


async def _sleep(seconds: float) -> None:  # patched by tests (fake clock)
    await asyncio.sleep(seconds)


def _cost(s: Settings, model: str, tin: int, tout: int) -> float:
    pin, pout = s.prices.get(model, (0.0, 0.0))
    return (tin * pin + tout * pout) / 1_000_000


async def _attempt(
    s: Settings,
    messages: list[Message],
    model: str,
    schema: type[BaseModel] | None,
    max_tokens: int,
    timeout: float,
) -> tuple[str, int, int]:
    provider = validate_provider(s)
    if provider == "mock":
        return await _call_mock(messages, model, schema)
    if provider == "ollama":
        return await _call_ollama(s, messages, model, schema, max_tokens, timeout)
    return await _call_hosted(s, messages, model, max_tokens, timeout)


async def _call_with_retry(
    s: Settings,
    messages: list[Message],
    model: str,
    schema: type[BaseModel] | None,
    max_tokens: int,
    timeout: float,
) -> tuple[str, int, int]:
    for attempt in range(s.llm_max_retries + 1):
        try:
            return await _attempt(s, messages, model, schema, max_tokens, timeout)
        except LLMTransportError as e:
            if not e.retryable or attempt >= s.llm_max_retries:
                raise
            delay = min(8.0, 0.5 * 2**attempt) * (0.5 + random.random() / 2)
            log.warning("llm retry", extra={"attempt": attempt + 1, "status": e.status})
            await _sleep(delay)
    raise AssertionError("unreachable")


def _parse[T: BaseModel](text: str, schema: type[T]) -> T:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    return schema.model_validate_json(t)


async def complete[T: BaseModel](
    messages: list[Message],
    *,
    model_role: Role,
    schema: type[T] | None = None,
    max_tokens: int = 1024,
    timeout: float | None = None,
    budget: Budget | None = None,
    run_id: str | None = None,
) -> LLMResult:
    s = get_settings()
    validate_provider(s)
    if s.llm_provider == "ollama":
        model = {"agent": s.ollama_agent_model, "fast": s.ollama_fast_model}[model_role]
    else:
        model = {"agent": s.agent_model, "fast": s.fast_model}[model_role]
    if timeout is None:
        timeout = s.llm_timeout_s
    elif timeout <= 0:
        raise ValueError("timeout must be > 0")
    msgs = list(messages)
    parsed: BaseModel | None = None
    for repair in (False, True):
        if budget:
            budget.check()
        t0 = time.monotonic()
        text, tin, tout = await _call_with_retry(
            s, msgs, model, schema, max_tokens, timeout
        )
        latency = time.monotonic() - t0
        usd = _cost(s, model, tin, tout)
        log.info(
            "llm call",
            extra={
                "run_id": run_id,
                "role": model_role,
                "model": model,
                "tokens_in": tin,
                "tokens_out": tout,
                "usd": round(usd, 6),
                "latency_ms": int(latency * 1000),
                "repair": repair,
                "provider": s.llm_provider,
            },
        )
        if budget:
            try:
                budget.charge(tin + tout, usd)
            except BudgetExceeded as e:
                try:
                    pre = _parse(text, schema) if schema is not None else None
                except (ValidationError, ValueError):
                    pre = None
                e.result = LLMResult(text, model, tin, tout, usd, latency, pre)
                log.warning("budget exceeded after paid call", extra={"run_id": run_id, "usd": round(usd, 6)})
                raise
        if schema is None:
            return LLMResult(text, model, tin, tout, usd, latency)
        try:
            parsed = _parse(text, schema)
            return LLMResult(text, model, tin, tout, usd, latency, parsed)
        except (ValidationError, ValueError) as e:
            err = str(e).splitlines()[0][:200]
            if repair:
                raise LLMValidationError(
                    f"schema {schema.__name__} invalid after repair"
                ) from e
            msgs = msgs + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": f"Invalid JSON ({err}). Reply with only valid JSON "
                    f"matching the schema.",
                },
            ]
    raise AssertionError("unreachable")

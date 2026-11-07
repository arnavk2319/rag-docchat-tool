# LLM wrapper

`app/core/llm.py` is the only module importing the hosted SDK or calling Ollama.

`await complete(messages, model_role="agent"|"fast", schema=None, max_tokens=1024, timeout=None, budget=None, run_id=None) -> LLMResult` (text, parsed, model, tokens_in/out, usd, latency_s).

- Role -> model id from config. Provider from `LLM_PROVIDER`: `anthropic` | `ollama` (stub, raises `NotImplementedError`, built in T-018), `mock`; anything else raises `ValueError`.
- Timeout: `timeout` arg or `llm_timeout_s` (30 s). SDK retries are off; the wrapper retries up to 3 times on 429, 5xx, timeouts and connection errors with exponential backoff (0.5 s base, cap 8 s) plus jitter. Other 4xx fail at once (`LLMTransportError`).
- One log line per call (`app.llm`): run_id, role, model, provider, tokens in/out, USD (price table in config), latency_ms, repair flag. Never prompt or response text.
- `Budget(max_steps, max_tokens, max_usd)`: checked before each call and charged after; raises `BudgetExceeded`.
- `schema=<Pydantic model>`: reply parsed as JSON (code fences stripped) and validated. On failure one repair call (error summary appended), then `LLMValidationError`. Each call counts toward the budget.
- Mock: no network. Default reply is `mock-reply-<sha256 of messages>` (valid example JSON when a schema is given). `mock_script(*items)` queues replies; an Exception item is raised (use `LLMTransportError(status)` to test retries); queue empty falls back to the default. Tests patch `llm._sleep` for a fake clock. `tests/conftest.py` forces `LLM_PROVIDER=mock` and blocks non-loopback sockets.
- Known gaps: hosted path uses plain text JSON replies (no forced tool-use schema yet); Ollama not implemented; per-user daily spend (SQLite) is a later ticket.

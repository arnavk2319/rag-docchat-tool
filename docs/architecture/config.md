# Config

`app/core/config.py`: `Settings` (pydantic-settings) reads the 12 env vars from the process environment only (no `.env` parsing in code; load it via your shell or runner). Cached by `get_settings()`.

| Env var | Field | Default |
| --- | --- | --- |
| ANTHROPIC_API_KEY | api_key (SecretStr; hidden from repr/dumps) | empty |
| LLM_PROVIDER | llm_provider (`anthropic` | `ollama` | `mock`; validated in `core.llm`) | mock |
| AGENT_MODEL / FAST_MODEL | agent_model / fast_model | claude-sonnet-5-5 / claude-haiku-5-5 |
| OLLAMA_BASE_URL | ollama_base_url | http://localhost:11434 |
| CHROMA_DIR / DB_PATH | chroma_dir / db_path | chroma_db / data/docchat.db |
| MAX_UPLOAD_MB | max_upload_mb | 10 |
| RATE_LIMIT_PER_MIN | rate_limit_per_min | 30 |
| DAILY_USD_CAP | daily_usd_cap | 1.0 |
| AGENT_MAX_STEPS / AGENT_MAX_USD | agent_max_steps / agent_max_usd | 8 / 0.05 |

Non-env fields: `prices` (USD per 1M tokens, input/output, per model id), `llm_timeout_s` (30), `llm_max_retries` (3). Model ids appear only in this file (test enforced). Logging: `core/logging.py` `setup_logging()` installs one idempotent JSON-line stderr handler; extras passed via `extra=` become keys; never log prompts or document text.

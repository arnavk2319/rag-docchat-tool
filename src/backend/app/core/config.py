"""Typed settings read from the process environment (no .env parsing here)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# USD per 1M tokens: (input, output). Keyed by model id; unknown ids cost 0 and are logged.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (3.0, 15.0),
    "claude-haiku-5-5": (1.0, 5.0),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="ANTHROPIC_API_KEY"
    )
    llm_provider: str = "mock"  # validated by core.llm (Anthropic | ollama | mock)
    agent_model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-5-5"
    ollama_base_url: str = "http://localhost:11434"
    ollama_agent_model: str = "llama3.1:8b"  # env OLLAMA_AGENT_MODEL
    ollama_fast_model: str = "llama3.2:3b"  # env OLLAMA_FAST_MODEL
    chroma_dir: str = "chroma_db"
    db_path: str = "data/docchat.db"
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rerank_model: str = "BAAI/bge-reranker-base"
    min_rerank_score: float = 0.0  # env MIN_RERANK_SCORE; abstain if top rerank score is below
    models_fake: bool = False  # env MODELS_FAKE; test-only
    max_upload_mb: int = Field(default=10, gt=0)
    rate_limit_per_min: int = Field(default=30, gt=0)  # env RATE_LIMIT_PER_MIN (per user, /query + /summarize)
    daily_usd_cap: float = Field(default=1.0, gt=0)  # env DAILY_USD_CAP per user per UTC day; must be > 0 (0 does NOT disable)
    global_daily_usd_cap: float = Field(default=5.0, gt=0)  # env GLOBAL_DAILY_USD_CAP all users, UTC day
    global_rate_limit_per_min: int = Field(default=120, gt=0)  # env GLOBAL_RATE_LIMIT_PER_MIN all users
    session_max_per_owner: int = Field(default=100, gt=0)  # env SESSION_MAX_PER_OWNER (live sessions)
    agent_max_steps: int = Field(default=16, gt=0)
    agent_max_usd: float = Field(default=0.05, ge=0)
    summary_max_usd: float = Field(default=0.02, ge=0)  # env SUMMARY_MAX_USD
    summary_reduce_role: Literal["agent", "fast"] = "fast"  # env SUMMARY_REDUCE_ROLE
    summary_compress: bool = False  # env SUMMARY_COMPRESS; optional compression before map
    summary_batch_chars: int = Field(default=8000, gt=0)  # env SUMMARY_BATCH_CHARS
    summary_max_batches: int = Field(default=40, gt=0)  # env SUMMARY_MAX_BATCHES

    session_max_messages: int = Field(default=20, ge=2, le=200)  # env SESSION_MAX_MESSAGES (10 turns)
    session_max_tokens: int = Field(default=2000, ge=100)  # env SESSION_MAX_TOKENS
    session_ttl_hours: float = Field(default=24.0, gt=0)  # env SESSION_TTL_HOURS (idle)

    # Non-env: price table (USD per 1M tokens) and transport defaults.
    prices: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: dict(DEFAULT_PRICES)
    )
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=0, le=10)


@lru_cache
def get_settings() -> Settings:
    return Settings()

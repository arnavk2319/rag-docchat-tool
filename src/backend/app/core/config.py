"""Typed settings read from the process environment (no .env parsing here)."""

from functools import lru_cache

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
    llm_provider: str = (
        "mock"  # validated by core.llm (Anthropic | ollama | mock)
    )
    agent_model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-5-5"
    ollama_base_url: str = "http://localhost:11434"
    chroma_dir: str = "chroma_db"
    db_path: str = "data/docchat.db"
    max_upload_mb: int = Field(default=10, gt=0)
    rate_limit_per_min: int = Field(default=30, gt=0)
    daily_usd_cap: float = Field(default=1.0, ge=0)
    agent_max_steps: int = Field(default=8, gt=0)
    agent_max_usd: float = Field(default=0.05, ge=0)

    # Non-env: price table (USD per 1M tokens) and transport defaults.
    prices: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: dict(DEFAULT_PRICES)
    )
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=0, le=10)


@lru_cache
def get_settings() -> Settings:
    return Settings()

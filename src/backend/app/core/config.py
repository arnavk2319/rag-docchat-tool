"""Typed settings read from the process environment (no .env parsing here)."""

import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# USD per 1M tokens: (input, output). Keyed by model id; unknown ids cost 0 and are logged.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (3.0, 15.0),
    "claude-haiku-5-5": (1.0, 5.0),
}


RESERVED_TENANTS = {"default", "public", "system", "admin", "global", "*"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="ANTHROPIC_API_KEY"
    )
    auth_token_secret: SecretStr = SecretStr("")  # env AUTH_TOKEN_SECRET (>=32 bytes); empty = all auth fails
    llm_provider: str = "mock"  # validated by core.llm (Anthropic | ollama | mock)
    agent_model: str = "claude-sonnet-5-5"
    fast_model: str = "claude-haiku-5-5"
    ollama_base_url: str = "http://localhost:11434"
    ollama_agent_model: str = "llama3.1:8b"  # env OLLAMA_AGENT_MODEL
    ollama_fast_model: str = "llama3.2:3b"  # env OLLAMA_FAST_MODEL
    database_url: SecretStr = SecretStr("")  # env DATABASE_URL; set = Postgres, empty = in-memory dev mode
    db_pool_max: int = Field(default=10, gt=0)  # env DB_POOL_MAX
    db_path: str = "data/docchat.db"
    object_store: Literal["s3", "local"] = "local"  # env OBJECT_STORE
    object_store_path: str = "data/objects"  # env OBJECT_STORE_PATH (local impl root)
    s3_endpoint: str = ""  # env S3_ENDPOINT (R2/MinIO; empty = AWS)
    s3_bucket: str = ""  # env S3_BUCKET
    s3_access_key_id: SecretStr = SecretStr("")  # env S3_ACCESS_KEY_ID
    s3_secret_access_key: SecretStr = SecretStr("")  # env S3_SECRET_ACCESS_KEY
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rerank_model: str = "BAAI/bge-reranker-base"
    min_rerank_score: float = 0.0  # env MIN_RERANK_SCORE; abstain if top rerank score is below
    models_fake: bool = False  # env MODELS_FAKE; test-only
    max_upload_mb: int = Field(default=10, gt=0)
    rate_limit_per_min: int = Field(default=30, gt=0)  # env RATE_LIMIT_PER_MIN (per user, /query + /summarize)
    daily_usd_cap: float = Field(default=1.0, gt=0)  # env DAILY_USD_CAP per user per UTC day; must be > 0 (0 does NOT disable)
    tenant_daily_usd_cap: float = Field(default=2.0, gt=0)  # env TENANT_DAILY_USD_CAP all users of one tenant, UTC day
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

    demo_tenant_id: str = "demo"  # env DEMO_TENANT_ID; tenant of anonymous tokens, never "default"
    anon_token_ttl_seconds: int = Field(default=3600, gt=0)  # env ANON_TOKEN_TTL_SECONDS
    anon_mint_rate_per_hour: int = Field(default=10, gt=0)  # env ANON_MINT_RATE_PER_HOUR (per IP)
    anon_user_daily_usd_cap: float = Field(default=0.10, gt=0)  # env ANON_USER_DAILY_USD_CAP per anonymous owner
    anon_tenant_daily_usd_cap: float = Field(default=1.0, gt=0)  # env ANON_TENANT_DAILY_USD_CAP whole demo tenant
    trusted_proxy: str = ""  # env TRUSTED_PROXY: header carrying the client IP (e.g. X-Forwarded-For); empty = ignore headers

    anon_mint_global_per_hour: int = Field(default=200, gt=0)  # env ANON_MINT_GLOBAL_PER_HOUR; all IPs together
    upload_rate_per_hour: int = Field(default=5, gt=0)  # env UPLOAD_RATE_PER_HOUR; POST /v1/documents requests per tenant:user
    anon_max_docs: int = Field(default=5, gt=0)  # env ANON_MAX_DOCS; documents one anon- owner may hold
    owner_max_docs: int = Field(default=200, ge=0)  # env OWNER_MAX_DOCS; documents any other owner may hold, 0 = unlimited
    demo_tenant_max_bytes: int = Field(default=200 * 1024 * 1024, gt=0)  # env DEMO_TENANT_MAX_BYTES; stored bytes, demo tenant
    ingest_max_concurrent: int = Field(default=2, gt=0)  # env INGEST_MAX_CONCURRENT; ingests running at once (extra requests wait)

    anon_data_ttl_hours: float = Field(default=24.0, gt=0)  # env ANON_DATA_TTL_HOURS; anonymous docs older than this are purged
    purge_interval_seconds: int = Field(default=0, ge=0)  # env PURGE_INTERVAL_SECONDS; background purge loop, 0 = off
    purge_on_start: bool = False  # env PURGE_ON_START; one purge pass at startup
    purge_batch_size: int = Field(default=200, gt=0)  # env PURGE_BATCH_SIZE; max docs deleted per run

    @field_validator("demo_tenant_id")
    @classmethod
    def _demo_tenant(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", v) or v.lower() in RESERVED_TENANTS:
            raise ValueError("DEMO_TENANT_ID must be a valid tenant id and never 'default'")
        return v

    cors_allow_origins: str = ""  # env CORS_ALLOW_ORIGINS, comma-separated exact origins; empty = no CORS
    enable_docs: bool = False  # env ENABLE_DOCS: /docs, /redoc, /openapi.json (+ detailed /healthz); off by default

    # Non-env: price table (USD per 1M tokens) and transport defaults.
    prices: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: dict(DEFAULT_PRICES)
    )
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=0, le=10)


def cors_origins(settings: "Settings") -> list[str]:
    return [o.strip().rstrip("/") for o in settings.cors_allow_origins.split(",") if o.strip() and o.strip() != "*"]


@lru_cache
def get_settings() -> Settings:
    return Settings()

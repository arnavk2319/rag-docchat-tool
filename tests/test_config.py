import re
from pathlib import Path

import pytest
from app.core.config import Settings, get_settings
from pydantic import ValidationError


def test_defaults(monkeypatch):
    for k in ("AGENT_MODEL", "FAST_MODEL", "AGENT_MAX_STEPS", "MAX_UPLOAD_MB"):
        monkeypatch.delenv(k, raising=False)
    s = Settings()
    assert s.llm_provider == "mock"
    assert s.agent_model == "claude-sonnet-5-5" and s.fast_model == "claude-haiku-5-5"
    assert s.agent_max_steps == 16 and s.max_upload_mb == 10 and s.agent_max_usd == 0.05


def test_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "x-agent")
    monkeypatch.setenv("AGENT_MAX_STEPS", "3")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-zzz9")
    s = get_settings()
    assert (
        s.agent_model == "x-agent"
        and s.agent_max_steps == 3
        and s.api_key.get_secret_value() == "key-zzz9"
    )
    assert "key-zzz9" not in repr(s)  # key hidden from repr


def test_model_ids_only_in_config():
    root = Path(__file__).parent.parent / "src/backend/app"
    pat = re.compile(r"claude-(sonnet|haiku|opus)")
    hits = [
        p.name
        for p in root.rglob("*.py")
        if "utilities" not in p.parts and pat.search(p.read_text())
    ]
    assert hits == ["config.py"]


@pytest.mark.parametrize(
    "var,val",
    [
        ("MAX_UPLOAD_MB", "-1"),
        ("MAX_UPLOAD_MB", "0"),
        ("RATE_LIMIT_PER_MIN", "-5"),
        ("DAILY_USD_CAP", "-0.1"),
        ("AGENT_MAX_STEPS", "-2"),
        ("AGENT_MAX_USD", "-1"),
        ("AGENT_MAX_STEPS", "abc"),
    ],
)
def test_invalid_values_fail_fast(monkeypatch, var, val):
    monkeypatch.setenv(var, val)
    with pytest.raises(ValidationError):
        Settings()


def test_all_twelve_env_vars_override(monkeypatch):
    env = {
        "ANTHROPIC_API_KEY": "k",
        "LLM_PROVIDER": "ollama",
        "AGENT_MODEL": "a",
        "FAST_MODEL": "f",
        "OLLAMA_BASE_URL": "http://h:1",
        "CHROMA_DIR": "c",
        "DB_PATH": "d.db",
        "MAX_UPLOAD_MB": "3",
        "RATE_LIMIT_PER_MIN": "4",
        "DAILY_USD_CAP": "2.5",
        "AGENT_MAX_STEPS": "5",
        "AGENT_MAX_USD": "0.5",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    s = Settings()
    assert (s.llm_provider, s.agent_model, s.fast_model) == ("ollama", "a", "f")
    assert (s.ollama_base_url, s.chroma_dir, s.db_path) == ("http://h:1", "c", "d.db")
    assert (s.max_upload_mb, s.rate_limit_per_min, s.daily_usd_cap) == (3, 4, 2.5)
    assert (s.agent_max_steps, s.agent_max_usd) == (5, 0.5)


def test_settings_do_not_read_dotenv():
    assert Settings.model_config.get("env_file") is None


async def test_bad_provider_fails_at_call_time_not_config(monkeypatch):
    from app.core.llm import complete

    monkeypatch.setenv("LLM_PROVIDER", "nope")
    Settings()  # config accepts it
    with pytest.raises(ValueError, match="nope"):
        await complete([{"role": "user", "content": "x"}], model_role="fast")


def test_api_key_secret(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-zzz9")
    s = Settings()
    assert "key-zzz9" not in repr(s) and "key-zzz9" not in str(s.model_dump())
    assert "key-zzz9" not in s.model_dump_json()
    assert s.api_key.get_secret_value() == "key-zzz9"


@pytest.mark.parametrize(
    ("var", "val"),
    [
        ("LLM_TIMEOUT_S", "0"),
        ("LLM_TIMEOUT_S", "-1"),
        ("LLM_MAX_RETRIES", "-1"),
        ("LLM_MAX_RETRIES", "11"),
    ],
)
def test_invalid_llm_limits(monkeypatch, var, val):
    monkeypatch.setenv(var, val)
    with pytest.raises(ValidationError):
        Settings()

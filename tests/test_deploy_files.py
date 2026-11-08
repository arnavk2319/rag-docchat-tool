"""Static checks on the deploy artifacts (no Docker needed)."""

import re
from pathlib import Path

from app.core.config import Settings

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text()
IGNORE = (ROOT / ".dockerignore").read_text().splitlines()
DEPLOY = (ROOT / "docs" / "deploy.md").read_text()

# Settings that are test-only or internal and need no deploy documentation.
NOT_DEPLOY = {
    "api_key",
    "prices",
    "llm_timeout_s",
    "llm_max_retries",
    "models_fake",
    "agent_model",
    "fast_model",
}


def test_dockerfile_non_root_user() -> None:
    users = re.findall(r"^USER\s+(\S+)", DOCKERFILE, re.MULTILINE)
    assert users and users[-1] not in {"root", "0"}


def test_dockerfile_no_env_copy_and_has_healthcheck() -> None:
    assert not re.search(r"^(COPY|ADD)\b.*\.env", DOCKERFILE, re.MULTILINE)
    assert re.search(r"^HEALTHCHECK\b", DOCKERFILE, re.MULTILINE)
    assert "8080" in DOCKERFILE


def test_dockerignore_excludes_env_and_junk() -> None:
    assert ".env" in IGNORE and ".env.*" in IGNORE
    for item in (".venv", "worktrees", "data", ".git"):
        assert item in IGNORE


def test_deploy_doc_mentions_every_deploy_setting() -> None:
    missing = [
        n.upper()
        for n in Settings.model_fields
        if n not in NOT_DEPLOY and n.upper() not in DEPLOY
    ]
    assert not missing, missing
    for needed in ("ANTHROPIC_API_KEY", "AGENT_MODEL", "Gate B", "Rollback"):
        assert needed in DEPLOY

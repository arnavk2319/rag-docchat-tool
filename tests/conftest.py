import ipaddress
import os
import socket

import pytest

os.environ["LLM_PROVIDER"] = "mock"
os.environ["MODELS_FAKE"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)

_LOCAL = {"localhost", "127.0.0.1", "::1", ""}


def _is_local(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    if host in _LOCAL or host is None:
        return True
    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch):
    real_connect = socket.socket.connect
    real_gai = socket.getaddrinfo

    def connect(self, address):
        if isinstance(address, tuple) and not _is_local(address[0]):
            raise RuntimeError(f"network blocked in tests: {address[0]}")
        return real_connect(self, address)

    def gai(host, *a, **k):
        if not _is_local(host):
            raise RuntimeError(f"network blocked in tests: {host}")
        return real_gai(host, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", gai)


@pytest.fixture(autouse=True)
def _fresh_settings():
    from app.core import config, llm, models

    config.get_settings.cache_clear()
    llm.mock_script()
    models.reset_models()
    yield
    config.get_settings.cache_clear()
    llm.mock_script()
    models.reset_models()


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path, monkeypatch):
    """App lifespan creates registry + Chroma; keep them out of the repo."""
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db" / "docchat.db"))

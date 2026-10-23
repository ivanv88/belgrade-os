import pytest
from config import Config, load_config


def test_defaults():
    cfg = Config(
        _env_file=None,
        db_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379",
    )
    assert cfg.bridge_url == "http://localhost:8081"
    assert cfg.port == 8000


def test_env_override(monkeypatch):
    monkeypatch.setenv("BEG_OS_BRIDGE_URL", "http://bridge:8081")
    cfg = Config(_env_file=None)
    assert cfg.bridge_url == "http://bridge:8081"


def test_ignores_other_services_keys_in_shared_env_file(tmp_path):
    """All services read the one repo-root .env; keys meant for other services must not fail validation."""
    env = tmp_path / ".env"
    env.write_text("CF_TUNNEL_TOKEN=x\nAPP_REDIS_URL=redis://app@localhost:6379\nMCP_JWT_SECRET=s\n")
    Config(_env_file=env)


def test_host_defaults_to_loopback(monkeypatch):
    """Internal services must not listen on the LAN unless explicitly configured."""
    monkeypatch.delenv("CONTROLLER_HOST", raising=False)
    assert Config(_env_file=None).host == "127.0.0.1"


def test_host_from_env(monkeypatch):
    monkeypatch.setenv("CONTROLLER_HOST", "172.30.0.1")
    assert Config(_env_file=None).host == "172.30.0.1"

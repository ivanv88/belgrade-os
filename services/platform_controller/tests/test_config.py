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


def test_app_runner_defaults_to_subprocess(monkeypatch):
    monkeypatch.delenv("CONTROLLER_APP_RUNNER", raising=False)
    monkeypatch.delenv("APP_DB_SECRET", raising=False)
    c = Config(_env_file=None)
    assert c.app_runner == "subprocess"
    assert c.app_env_dir == "/run/belgrade-os/apps"
    assert c.app_db_secret == ""


def test_app_runner_systemd_from_env(monkeypatch):
    monkeypatch.setenv("CONTROLLER_APP_RUNNER", "systemd")
    monkeypatch.setenv("APP_DB_SECRET", "s" * 64)
    c = Config(_env_file=None)
    assert (c.app_runner, c.app_db_secret) == ("systemd", "s" * 64)


def test_short_app_db_secret_is_rejected(monkeypatch):
    monkeypatch.delenv("CONTROLLER_APP_RUNNER", raising=False)
    monkeypatch.setenv("APP_DB_SECRET", "short")
    with pytest.raises(ValueError, match="32"):
        Config(_env_file=None)


def test_systemd_runner_requires_app_db_secret(monkeypatch):
    """Production must never fall back to handing apps the superuser DB URL."""
    monkeypatch.setenv("CONTROLLER_APP_RUNNER", "systemd")
    monkeypatch.delenv("APP_DB_SECRET", raising=False)
    with pytest.raises(ValueError, match="APP_DB_SECRET"):
        Config(_env_file=None)

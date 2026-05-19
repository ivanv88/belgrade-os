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

import socket
import pytest
from config import Config


def test_defaults():
    c = Config(anthropic_api_key="test-key")
    assert c.redis_url == "redis://localhost:6379"
    assert c.model == "claude-opus-4-7"
    assert c.max_tokens == 8192


def test_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    monkeypatch.setenv("MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("MAX_TOKENS", "4096")
    c = Config()
    assert c.anthropic_api_key == "env-key"
    assert c.model == "claude-haiku-4-5-20251001"
    assert c.max_tokens == 4096


def test_effective_consumer_id_hostname():
    c = Config(anthropic_api_key="k", consumer_id="")
    assert c.effective_consumer_id == socket.gethostname()


def test_effective_consumer_id_explicit():
    c = Config(anthropic_api_key="k", consumer_id="worker-1")
    assert c.effective_consumer_id == "worker-1"

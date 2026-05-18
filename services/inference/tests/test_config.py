import socket
import pytest
from pydantic import ValidationError
from config import Config


def test_defaults():
    c = Config(provider="anthropic", model="claude-opus-4-7", anthropic_api_key="k", _env_file=None)
    assert c.redis_url == "redis://localhost:6379"
    assert c.max_tokens == 8192
    assert c.ollama_base_url == "http://localhost:11434"


def test_provider_required(monkeypatch):
    monkeypatch.delenv("PROVIDER", raising=False)
    with pytest.raises(ValidationError):
        Config(model="some-model", _env_file=None)


def test_model_required(monkeypatch):
    monkeypatch.delenv("MODEL", raising=False)
    with pytest.raises(ValidationError):
        Config(provider="gemini", _env_file=None)


def test_anthropic_requires_api_key():
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        Config(provider="anthropic", model="claude-opus-4-7", _env_file=None)


def test_gemini_requires_api_key():
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        Config(provider="gemini", model="gemini-1.5-pro", _env_file=None)


def test_ollama_needs_no_key():
    c = Config(provider="ollama", model="qwen2.5-coder", _env_file=None)
    assert c.anthropic_api_key is None
    assert c.google_api_key is None


def test_effective_consumer_id_hostname():
    c = Config(provider="ollama", model="m", consumer_id="", _env_file=None)
    assert c.effective_consumer_id == socket.gethostname()


def test_effective_consumer_id_explicit():
    c = Config(provider="ollama", model="m", consumer_id="worker-1", _env_file=None)
    assert c.effective_consumer_id == "worker-1"


def test_from_env(monkeypatch):
    monkeypatch.setenv("PROVIDER", "gemini")
    monkeypatch.setenv("MODEL", "gemini-1.5-flash")
    monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
    monkeypatch.setenv("MAX_TOKENS", "4096")
    c = Config(_env_file=None)
    assert c.provider == "gemini"
    assert c.model == "gemini-1.5-flash"
    assert c.google_api_key == "gkey"
    assert c.max_tokens == 4096

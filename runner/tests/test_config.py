from __future__ import annotations
import socket
import pytest
from config import Config


def test_defaults():
    c = Config(_env_file=None)
    assert c.redis_url == "redis://localhost:6379"
    assert c.bridge_url == "http://localhost:8081"
    assert c.lease_ttl_s == 60
    assert c.tool_timeout_s == 30


def test_effective_worker_id_uses_hostname_when_empty():
    c = Config(worker_id="", _env_file=None)
    assert c.effective_worker_id == socket.gethostname()


def test_effective_worker_id_explicit():
    c = Config(worker_id="runner-1", _env_file=None)
    assert c.effective_worker_id == "runner-1"


def test_from_env(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379")
    monkeypatch.setenv("BRIDGE_URL", "http://bridge:8081")
    monkeypatch.setenv("WORKER_ID", "runner-42")
    monkeypatch.setenv("TOOL_TIMEOUT_S", "10")
    c = Config(_env_file=None)
    assert c.redis_url == "redis://redis:6379"
    assert c.bridge_url == "http://bridge:8081"
    assert c.effective_worker_id == "runner-42"
    assert c.tool_timeout_s == 10

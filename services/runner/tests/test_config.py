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


import os
from unittest.mock import patch


def test_runner_redis_url_takes_precedence():
    env = {"RUNNER_REDIS_URL": "redis://runner:pw@localhost:6379", "REDIS_URL": "redis://generic:6379"}
    with patch.dict(os.environ, env):
        cfg = Config()
        assert cfg.redis_url == "redis://runner:pw@localhost:6379"


def test_falls_back_to_redis_url():
    with patch.dict(os.environ, {"REDIS_URL": "redis://fallback:6379"}, clear=True):
        cfg = Config()
        assert cfg.redis_url == "redis://fallback:6379"


def test_default_when_neither_set():
    with patch.dict(os.environ, {}, clear=True):
        cfg = Config()
        assert cfg.redis_url == "redis://localhost:6379"

from __future__ import annotations
import socket
import pytest
from config import Config


def test_defaults():
    c = Config(_env_file=None)
    assert c.redis_url == "redis://localhost:6379"
    assert c.ntfy_base_url == "https://ntfy.sh"
    assert c.ntfy_topic == "belgrade-os"
    assert c.notification_driver == "ntfy"


def test_effective_worker_id_uses_hostname_when_empty():
    c = Config(worker_id="", _env_file=None)
    assert c.effective_worker_id == socket.gethostname()


def test_effective_worker_id_explicit():
    c = Config(worker_id="notif-1", _env_file=None)
    assert c.effective_worker_id == "notif-1"


def test_from_env(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379")
    monkeypatch.setenv("NTFY_BASE_URL", "http://ntfy.local")
    monkeypatch.setenv("NTFY_TOPIC", "my-house")
    monkeypatch.setenv("NOTIFICATION_DRIVER", "firebase")
    c = Config(_env_file=None)
    assert c.redis_url == "redis://redis:6379"
    assert c.ntfy_base_url == "http://ntfy.local"
    assert c.ntfy_topic == "my-house"
    assert c.notification_driver == "firebase"


def test_load_config_returns_config_instance():
    from config import load_config
    cfg = load_config()
    assert isinstance(cfg, Config)

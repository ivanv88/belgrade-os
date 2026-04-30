from __future__ import annotations
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from main import AppProcess


def test_start_injects_redis_url(tmp_path):
    """BEG_OS_REDIS_URL must be explicitly set in child env."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    with patch("main.subprocess.Popen", side_effect=fake_popen), \
         patch("main.open", MagicMock()), \
         patch.dict(os.environ, {"BEG_OS_REDIS_URL": "redis://redis:6379"}, clear=False):
        import asyncio
        asyncio.run(app.start())

    assert captured_env["BEG_OS_REDIS_URL"] == "redis://redis:6379"


def test_start_uses_global_driver_when_no_manifest(tmp_path):
    """Falls back to BEG_OS_NOTIFICATION_DRIVER env var when manifest absent."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    with patch("main.subprocess.Popen", side_effect=fake_popen), \
         patch("main.open", MagicMock()), \
         patch.dict(os.environ, {"BEG_OS_NOTIFICATION_DRIVER": "firebase"}, clear=False):
        import asyncio
        asyncio.run(app.start())

    assert captured_env["BEG_OS_NOTIFICATION_DRIVER"] == "firebase"


def test_start_uses_manifest_driver_over_global(tmp_path):
    """manifest.json notifications.driver overrides the global env var."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    with patch("main.subprocess.Popen", side_effect=fake_popen), \
         patch("main.open", MagicMock()), \
         patch("main.AppProcess._load_manifest", return_value={"notifications": {"driver": "firebase"}}), \
         patch.dict(os.environ, {"BEG_OS_NOTIFICATION_DRIVER": "ntfy"}, clear=False):
        import asyncio
        asyncio.run(app.start())

    assert captured_env["BEG_OS_NOTIFICATION_DRIVER"] == "firebase"

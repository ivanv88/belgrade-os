from __future__ import annotations
import asyncio
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


def test_load_manifest_reads_json_file(tmp_path):
    """_load_manifest must parse JSON from manifest.json — requires json to be imported."""
    import json as _json
    manifest_data = {"notifications": {"driver": "firebase"}, "version": "1.0"}
    (tmp_path / "manifest.json").write_text(_json.dumps(manifest_data))

    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    result = app._load_manifest()

    assert result == manifest_data
    assert result["notifications"]["driver"] == "firebase"


def test_load_manifest_returns_empty_dict_when_absent(tmp_path):
    """_load_manifest returns {} when manifest.json does not exist."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    result = app._load_manifest()
    assert result == {}


def test_load_manifest_returns_empty_dict_on_invalid_json(tmp_path):
    """_load_manifest swallows parse errors and returns {}."""
    (tmp_path / "manifest.json").write_text("not valid json {{{")
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    result = app._load_manifest()
    assert result == {}


def test_watchdog_restarts_dead_app(tmp_path):
    """If an app process exits, the watchdog must restart it within one tick."""
    from main import AppSupervisor
    sup = AppSupervisor(apps_root=tmp_path)

    app_dir = tmp_path / "myapp"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("# stub")

    live_proc = MagicMock()
    live_proc.pid = 9999
    live_proc.poll.return_value = None

    dead_proc = MagicMock()
    dead_proc.pid = 9998
    dead_proc.poll.return_value = 1
    dead_proc.returncode = 1

    start_call_count = 0

    async def run():
        nonlocal start_call_count
        original_start = sup.start_app

        async def mock_start(app_id):
            nonlocal start_call_count
            start_call_count += 1
            proc = live_proc if start_call_count == 1 else MagicMock(pid=9997, poll=MagicMock(return_value=None))
            with patch("main.subprocess.Popen", return_value=proc), \
                 patch("main.open", MagicMock()):
                await original_start(app_id)
            if start_call_count == 1:
                sup.running_apps["myapp"].process = dead_proc

        sup.start_app = mock_start

        with patch("main.subprocess.Popen", return_value=live_proc), \
             patch("main.open", MagicMock()):
            await sup.discover_and_start()

        assert start_call_count == 1

        await sup._watch_tick()

        assert start_call_count == 2, "watchdog must restart dead app"

    asyncio.run(run())


def test_watchdog_does_not_restart_live_app(tmp_path):
    """Running apps must not be restarted by the watchdog."""
    from main import AppSupervisor
    sup = AppSupervisor(apps_root=tmp_path)

    app_dir = tmp_path / "myapp"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("# stub")

    live_proc = MagicMock()
    live_proc.pid = 9999
    live_proc.poll.return_value = None

    start_call_count = 0

    async def run():
        nonlocal start_call_count
        original_start = sup.start_app

        async def mock_start(app_id):
            nonlocal start_call_count
            start_call_count += 1
            with patch("main.subprocess.Popen", return_value=live_proc), \
                 patch("main.open", MagicMock()):
                await original_start(app_id)

        sup.start_app = mock_start

        with patch("main.subprocess.Popen", return_value=live_proc), \
             patch("main.open", MagicMock()):
            await sup.discover_and_start()

        await sup._watch_tick()

        assert start_call_count == 1, "live app must not be restarted"

    asyncio.run(run())

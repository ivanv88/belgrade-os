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
    """manifest.notifications.driver overrides the global env var."""
    from main import _AppManifest, _NotificationsManifest
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    fake_manifest = _AppManifest(app_id="shopping", notifications=_NotificationsManifest(driver="firebase"))

    with patch("main.subprocess.Popen", side_effect=fake_popen), \
         patch("main.open", MagicMock()), \
         patch("main.AppProcess._load_manifest", return_value=fake_manifest), \
         patch.dict(os.environ, {"BEG_OS_NOTIFICATION_DRIVER": "ntfy"}, clear=False):
        import asyncio
        asyncio.run(app.start())

    assert captured_env["BEG_OS_NOTIFICATION_DRIVER"] == "firebase"


def test_load_manifest_reads_json_file(tmp_path):
    """_load_manifest returns a typed _AppManifest for valid JSON."""
    from main import _AppManifest
    manifest_data = {"app_id": "shopping", "notifications": {"driver": "firebase"}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest_data))

    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    result = app._load_manifest()

    assert isinstance(result, _AppManifest)
    assert result.app_id == "shopping"
    assert result.notifications.driver == "firebase"


def test_load_manifest_returns_none_when_absent(tmp_path):
    """_load_manifest returns None when manifest.json does not exist."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    assert app._load_manifest() is None


def test_load_manifest_raises_on_invalid_json(tmp_path):
    """_load_manifest raises ValueError on malformed JSON."""
    (tmp_path / "manifest.json").write_text("not valid json {{{")
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    with pytest.raises(ValueError, match="not valid JSON"):
        app._load_manifest()


def test_load_manifest_raises_on_schema_violation(tmp_path):
    """_load_manifest raises ValueError when JSON is valid but schema is wrong."""
    # app_id is required — omitting it should fail validation
    (tmp_path / "manifest.json").write_text(json.dumps({"name": "Oops, no app_id"}))
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    with pytest.raises(ValueError, match="failed schema validation"):
        app._load_manifest()


def test_load_manifest_raises_when_app_id_mismatches_directory(tmp_path):
    """_load_manifest raises ValueError when manifest app_id differs from directory name."""
    (tmp_path / "manifest.json").write_text(json.dumps({"app_id": "other-app"}))
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    with pytest.raises(ValueError, match="does not match directory name"):
        app._load_manifest()


def test_start_raises_on_invalid_manifest(tmp_path):
    """AppProcess.start() propagates ValueError from _load_manifest."""
    (tmp_path / "manifest.json").write_text("{{broken json")
    app = AppProcess(app_id="badapp", path=tmp_path, port=9002)
    with pytest.raises(ValueError, match="not valid JSON"):
        asyncio.run(app.start())


def test_start_app_skips_app_on_invalid_manifest(tmp_path):
    """AppSupervisor.start_app() skips an app whose manifest fails validation."""
    from main import AppSupervisor
    sup = AppSupervisor(apps_root=tmp_path)

    app_dir = tmp_path / "badapp"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("# stub")
    (app_dir / "manifest.json").write_text("{{broken json")

    asyncio.run(sup.start_app("badapp"))

    assert "badapp" not in sup.running_apps


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


def test_start_injects_mcp_enabled_when_manifest_has_mcp_true(tmp_path):
    """BEG_OS_MCP_ENABLED=true injected when manifest has mcp: true."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    manifest_data = {"app_id": "shopping", "mcp": True}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest_data))

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    with patch("main.subprocess.Popen", side_effect=fake_popen), \
         patch("main.open", MagicMock()):
        asyncio.run(app.start())

    assert captured_env.get("BEG_OS_MCP_ENABLED") == "true"


def test_start_does_not_inject_mcp_when_manifest_has_mcp_false(tmp_path):
    """BEG_OS_MCP_ENABLED stripped from env even when set in parent env."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    manifest_data = {"app_id": "shopping", "mcp": False}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest_data))

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    with patch("main.subprocess.Popen", side_effect=fake_popen), \
         patch("main.open", MagicMock()), \
         patch.dict(os.environ, {"BEG_OS_MCP_ENABLED": "true"}):
        asyncio.run(app.start())

    assert "BEG_OS_MCP_ENABLED" not in captured_env


def test_start_does_not_inject_mcp_when_no_manifest(tmp_path):
    """BEG_OS_MCP_ENABLED stripped from env even when set in parent env."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    with patch("main.subprocess.Popen", side_effect=fake_popen), \
         patch("main.open", MagicMock()), \
         patch.dict(os.environ, {"BEG_OS_MCP_ENABLED": "true"}):
        asyncio.run(app.start())

    assert "BEG_OS_MCP_ENABLED" not in captured_env


def test_start_app_skips_container_runtime(tmp_path):
    """AppSupervisor.start_app() does not add a container app to running_apps."""
    from main import AppSupervisor

    sup = AppSupervisor(apps_root=tmp_path)

    app_dir = tmp_path / "mycontainer"
    app_dir.mkdir()
    manifest_data = {"app_id": "mycontainer", "runtime": "container", "endpoint": "http://localhost:9090"}
    (app_dir / "manifest.json").write_text(json.dumps(manifest_data))

    asyncio.run(sup.start_app("mycontainer"))

    assert "mycontainer" not in sup.running_apps


def test_discover_finds_manifest_only_app(tmp_path):
    """discover_and_start discovers an app that has manifest.json but no main.py."""
    from main import AppSupervisor

    sup = AppSupervisor(apps_root=tmp_path)

    # Container app: has manifest.json but no main.py
    app_dir = tmp_path / "containerapp"
    app_dir.mkdir()
    manifest_data = {"app_id": "containerapp", "runtime": "container", "endpoint": "http://localhost:9091"}
    (app_dir / "manifest.json").write_text(json.dumps(manifest_data))

    discovered = []
    original_start = sup.start_app

    async def tracking_start(app_id):
        discovered.append(app_id)
        await original_start(app_id)

    sup.start_app = tracking_start

    asyncio.run(sup.discover_and_start())

    assert "containerapp" in discovered


def test_manifest_allows_container_runtime(tmp_path):
    """_AppManifest accepts runtime='container' with an endpoint."""
    from main import _AppManifest

    manifest = _AppManifest(
        app_id="mycontainer",
        runtime="container",
        endpoint="http://localhost:9090",
    )

    assert manifest.runtime == "container"
    assert manifest.endpoint == "http://localhost:9090"

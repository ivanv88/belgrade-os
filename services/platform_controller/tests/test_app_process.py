from __future__ import annotations
import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from main import AppProcess


def _start(app):
    """What the supervisor does for one app, minus provisioning: build env, run it."""
    from main import DB_URL
    from app_runner import SubprocessRunner
    asyncio.run(SubprocessRunner().start(app.app_id, app.path, app.build_env(DB_URL)))


def test_start_injects_redis_url(tmp_path):
    """BEG_OS_REDIS_URL must be explicitly set in child env."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    captured_env = {}

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        mock = MagicMock()
        mock.pid = 1234
        return mock

    with patch("app_runner.subprocess.Popen", side_effect=fake_popen), \
         patch("app_runner.open", MagicMock(), create=True), \
         patch.dict(os.environ, {"BEG_OS_REDIS_URL": "redis://redis:6379"}, clear=False):
        import asyncio
        _start(app)

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

    with patch("app_runner.subprocess.Popen", side_effect=fake_popen), \
         patch("app_runner.open", MagicMock(), create=True), \
         patch.dict(os.environ, {"BEG_OS_NOTIFICATION_DRIVER": "firebase"}, clear=False):
        import asyncio
        _start(app)

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

    with patch("app_runner.subprocess.Popen", side_effect=fake_popen), \
         patch("app_runner.open", MagicMock(), create=True), \
         patch("main.AppProcess._load_manifest", return_value=fake_manifest), \
         patch.dict(os.environ, {"BEG_OS_NOTIFICATION_DRIVER": "ntfy"}, clear=False):
        import asyncio
        _start(app)

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
    """AppProcess.build_env() propagates ValueError from _load_manifest."""
    (tmp_path / "manifest.json").write_text("{{broken json")
    app = AppProcess(app_id="badapp", path=tmp_path, port=9002)
    with pytest.raises(ValueError, match="not valid JSON"):
        app.build_env("postgresql+asyncpg://u:p@h/d")


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

    with patch("app_runner.subprocess.Popen", side_effect=fake_popen), \
         patch("app_runner.open", MagicMock(), create=True):
        _start(app)

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

    with patch("app_runner.subprocess.Popen", side_effect=fake_popen), \
         patch("app_runner.open", MagicMock(), create=True), \
         patch.dict(os.environ, {"BEG_OS_MCP_ENABLED": "true"}):
        _start(app)

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

    with patch("app_runner.subprocess.Popen", side_effect=fake_popen), \
         patch("app_runner.open", MagicMock(), create=True), \
         patch.dict(os.environ, {"BEG_OS_MCP_ENABLED": "true"}):
        _start(app)

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


def test_start_injects_port(tmp_path):
    """BEG_OS_PORT must match the port in BEG_OS_CALLBACK_URL so the app listens where the bridge calls."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9007)
    captured_env = {}

    def fake_popen(cmd, env, **kwargs):
        captured_env.update(env)
        return MagicMock(pid=1234)

    with patch("app_runner.subprocess.Popen", side_effect=fake_popen), \
         patch("app_runner.open", MagicMock(), create=True):
        _start(app)

    assert captured_env["BEG_OS_PORT"] == "9007"
    assert captured_env["BEG_OS_CALLBACK_URL"] == "http://localhost:9007"


def test_restarted_app_keeps_its_port(tmp_path):
    """Restarts must not move an app to a new port — its bridge registration would go stale."""
    from main import AppSupervisor
    sup = AppSupervisor(apps_root=tmp_path)
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "main.py").write_text("# stub")

    async def run():
        with patch("app_runner.subprocess.Popen", return_value=MagicMock(pid=1)), \
             patch("app_runner.open", MagicMock(), create=True):
            await sup.start_app("alpha")
            await sup.start_app("beta")
            alpha_port = sup.running_apps["alpha"].port
            await sup.start_app("alpha")  # restart
            assert sup.running_apps["alpha"].port == alpha_port
            assert sup.running_apps["beta"].port != alpha_port

    asyncio.run(run())


def test_discover_skips_app_without_main_py(tmp_path):
    """Manifest-only apps (static UI bundles) have no process to run — spawning them crash-loops."""
    from main import AppSupervisor
    sup = AppSupervisor(apps_root=tmp_path)
    ui_only = tmp_path / "dashboard"
    ui_only.mkdir()
    (ui_only / "manifest.json").write_text(json.dumps({"app_id": "dashboard"}))

    with patch("app_runner.subprocess.Popen") as mock_popen, \
         patch("app_runner.open", MagicMock(), create=True):
        asyncio.run(sup.discover_and_start())

    mock_popen.assert_not_called()
    assert "dashboard" not in sup.running_apps


def test_start_runs_app_with_controller_interpreter(tmp_path):
    """Apps must run in the controller's venv — a bare `python3` resolves to the system interpreter."""
    import sys
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)

    with patch("app_runner.subprocess.Popen", return_value=MagicMock(pid=1)) as mock_popen, \
         patch("app_runner.open", MagicMock(), create=True):
        _start(app)

    assert mock_popen.call_args.args[0][0] == sys.executable


_PLATFORM_SECRETS = {
    "MCP_JWT_SECRET": "s1",
    "CF_TUNNEL_TOKEN": "s2",
    "DB_PASSWORD": "s3",
    "CONTROLLER_API_TOKEN": "s4",
    "CONTROLLER_REDIS_URL": "redis://controller:s5@localhost:6379",
    "BRIDGE_REDIS_URL": "redis://bridge:s6@localhost:6379",
    "ANTHROPIC_API_KEY": "s7",
}


def _start_and_capture_env(tmp_path, parent_env: dict) -> dict:
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    with patch.dict(os.environ, parent_env, clear=False):
        return app.build_env("postgresql+asyncpg://u:p@h/d")


def test_platform_secrets_are_not_passed_to_apps(tmp_path):
    """Apps are less trusted than the platform: a buggy app or compromised dependency
    must not be able to mint MCP tokens, hijack the tunnel or act as another service."""
    env = _start_and_capture_env(tmp_path, _PLATFORM_SECRETS)

    leaked = sorted(k for k in _PLATFORM_SECRETS if k in env)
    assert leaked == []
    assert not any(v in env.values() for v in _PLATFORM_SECRETS.values())


def test_apps_get_process_basics_and_the_beg_os_namespace(tmp_path):
    env = _start_and_capture_env(tmp_path, {
        "PATH": "/usr/bin", "HOME": "/home/ivan", "BEG_OS_VAULT_PATH": "/vault",
    })

    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/ivan"
    assert env["BEG_OS_VAULT_PATH"] == "/vault"
    assert env["BEG_OS_APP_ID"] == "shopping"


def test_apps_get_the_app_redis_user_not_the_controllers(tmp_path):
    env = _start_and_capture_env(tmp_path, {
        **_PLATFORM_SECRETS, "APP_REDIS_URL": "redis://app:apppw@localhost:6379",
    })

    assert env["BEG_OS_REDIS_URL"] == "redis://app:apppw@localhost:6379"
    assert "APP_REDIS_URL" not in env


def test_apps_can_import_the_sdk(tmp_path):
    """Apps import belgrade_sdk; the controller provides it on PYTHONPATH rather than
    every app patching sys.path (demo_app crash-looped on ModuleNotFoundError)."""
    from main import SDK_ROOT
    env = _start_and_capture_env(tmp_path, {"PYTHONPATH": "/repo"})

    entries = env["PYTHONPATH"].split(os.pathsep)
    assert str(SDK_ROOT) in entries
    assert "/repo" in entries
    assert (SDK_ROOT / "belgrade_sdk" / "__init__.py").exists()


# --- identity env (systemd mode) ---

def test_build_env_drops_identity_vars_for_systemd(tmp_path):
    """EnvironmentFile assignments override what systemd sets for the DynamicUser."""
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    parent = {"HOME": "/home/ivan", "USER": "ivan", "LOGNAME": "ivan", "TMPDIR": "/tmp/x", "PATH": "/usr/bin"}
    with patch.dict(os.environ, parent, clear=False):
        env = app.build_env("postgresql+asyncpg://u:p@h/d", inherit_identity=False)
    for k in ("HOME", "USER", "LOGNAME", "TMPDIR"):
        assert k not in env
    assert env["PATH"] == "/usr/bin"
    assert env["BEG_OS_DB_URL"] == "postgresql+asyncpg://u:p@h/d"


def test_build_env_keeps_identity_vars_by_default(tmp_path):
    app = AppProcess(app_id="shopping", path=tmp_path, port=9001)
    with patch.dict(os.environ, {"HOME": "/home/ivan", "USER": "ivan"}, clear=False):
        env = app.build_env("x")
    assert env["HOME"] == "/home/ivan" and env["USER"] == "ivan"


# --- supervisor + runner ---

class FakeRunner:
    isolated = False

    def __init__(self):
        self.started: dict[str, dict] = {}
        self.alive: set[str] = set()

    async def start(self, app_id, app_dir, env):
        self.started[app_id] = env
        self.alive.add(app_id)

    async def stop(self, app_id):
        self.alive.discard(app_id)

    async def is_running(self, app_id):
        return app_id in self.alive

    async def pid(self, app_id):
        return 4242 if app_id in self.alive else None


async def _fake_provision(app_id):
    return f"postgresql+asyncpg://app_{app_id}:pw@h/d"


def _mk_app(root, name):
    (root / name).mkdir()
    (root / name / "main.py").write_text("# stub")


def test_supervisor_starts_apps_through_its_runner(tmp_path):
    from main import AppSupervisor
    _mk_app(tmp_path, "shop")
    runner = FakeRunner()
    sup = AppSupervisor(apps_root=tmp_path, runner=runner)
    asyncio.run(sup.discover_and_start())
    assert runner.started["shop"]["BEG_OS_APP_ID"] == "shop"
    assert "shop" in sup.running_apps


def test_app_gets_its_own_db_url_from_the_provisioner(tmp_path):
    from main import AppSupervisor
    _mk_app(tmp_path, "shop")
    runner = FakeRunner()

    async def provision(app_id):
        return f"postgresql+asyncpg://app_{app_id}:pw@localhost:5434/belgrade_os"

    sup = AppSupervisor(apps_root=tmp_path, runner=runner, db_url_for_app=provision)
    asyncio.run(sup.start_app("shop"))
    assert runner.started["shop"]["BEG_OS_DB_URL"].startswith("postgresql+asyncpg://app_shop:")


def test_watchdog_restarts_app_its_runner_reports_dead(tmp_path):
    from main import AppSupervisor
    _mk_app(tmp_path, "shop")
    runner = FakeRunner()
    sup = AppSupervisor(apps_root=tmp_path, runner=runner)

    async def run():
        await sup.start_app("shop")
        runner.alive.discard("shop")
        runner.started.clear()
        await sup._watch_tick()
        assert "shop" in runner.started
        runner.started.clear()
        await sup._watch_tick()
        assert runner.started == {}

    asyncio.run(run())


def test_runner_start_failure_skips_app_and_logs(tmp_path, caplog):
    from main import AppSupervisor

    class Flaky(FakeRunner):
        async def start(self, app_id, app_dir, env):
            if app_id == "bad":
                raise RuntimeError("systemctl restart failed: boom")
            await super().start(app_id, app_dir, env)

    for n in ("bad", "good"):
        _mk_app(tmp_path, n)
    sup = AppSupervisor(apps_root=tmp_path, runner=Flaky())
    with caplog.at_level("ERROR"):
        asyncio.run(sup.discover_and_start())
    assert "bad" not in sup.running_apps and "good" in sup.running_apps
    assert "Skipping app bad" in caplog.text and "boom" in caplog.text


def test_watchdog_continues_when_one_restart_raises(tmp_path, caplog):
    from main import AppSupervisor
    for n in ("a", "b"):
        _mk_app(tmp_path, n)
    runner = FakeRunner()
    sup = AppSupervisor(apps_root=tmp_path, runner=runner)

    async def run():
        await sup.start_app("a")
        await sup.start_app("b")
        runner.alive.clear()
        runner.started.clear()
        original = sup.start_app

        async def flaky(app_id):
            if app_id == "a":
                raise OSError("disk gone")
            await original(app_id)

        sup.start_app = flaky
        await sup._watch_tick()

    with caplog.at_level("ERROR"):
        asyncio.run(run())
    assert "b" in runner.started
    assert "disk gone" in caplog.text


def test_invalid_db_app_id_is_skipped_when_provisioner_set(tmp_path, caplog):
    from main import AppSupervisor
    from app_db import app_role_name
    _mk_app(tmp_path, "my-app")
    _mk_app(tmp_path, "shop")
    runner = FakeRunner()

    async def provision(app_id):
        return f"postgresql+asyncpg://{app_role_name(app_id)}:pw@h/d"

    sup = AppSupervisor(apps_root=tmp_path, runner=runner, db_url_for_app=provision)
    with caplog.at_level("ERROR"):
        asyncio.run(sup.discover_and_start())
    assert "my-app" not in sup.running_apps and "shop" in sup.running_apps
    assert "Skipping app my-app" in caplog.text


def test_failed_stop_keeps_app_visible(tmp_path, caplog):
    from main import AppSupervisor
    _mk_app(tmp_path, "shop")

    class BadStop(FakeRunner):
        async def stop(self, app_id):
            raise RuntimeError("stop failed")

    sup = AppSupervisor(apps_root=tmp_path, runner=BadStop())

    async def run():
        await sup.start_app("shop")
        await sup.stop_app("shop")

    with caplog.at_level("ERROR"):
        asyncio.run(run())
    assert "shop" in sup.running_apps
    assert "stop failed" in caplog.text


def test_systemd_runner_gets_env_without_identity_vars(tmp_path):
    from main import AppSupervisor
    from app_runner import SystemdRunner
    _mk_app(tmp_path, "shop")
    captured = {}

    class Spy(SystemdRunner):
        async def start(self, app_id, app_dir, env):
            captured.update(env)

    sup = AppSupervisor(apps_root=tmp_path, runner=Spy(env_dir=tmp_path), db_url_for_app=_fake_provision)
    with patch.dict(os.environ, {"HOME": "/home/ivan", "USER": "ivan"}, clear=False):
        asyncio.run(sup.start_app("shop"))
    assert "HOME" not in captured and "USER" not in captured
    assert captured["BEG_OS_APP_ID"] == "shop"


def test_provision_app_db_error_never_leaks_the_password(monkeypatch):
    import main
    from sqlalchemy.exc import DBAPIError

    class Boom(Exception):
        pass

    async def failing(conn, role, password, database):
        raise DBAPIError("ALTER ROLE app_shop PASSWORD 'SECRET-PW'", {}, Boom("x"))

    class Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class Eng:
        def begin(self): return Conn()

    monkeypatch.setattr(main, "ensure_app_role", failing)
    monkeypatch.setattr(main, "engine", Eng())
    monkeypatch.setattr(main._cfg, "app_db_secret", "s" * 40, raising=False)
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(main._provision_app_db("shop"))
    assert "SECRET-PW" not in str(ei.value) and "Boom" in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


# --- review round: isolation, locking, failure visibility, wiring ---

def test_discovery_isolates_every_kind_of_per_app_failure(tmp_path, caplog):
    from main import AppSupervisor

    class Hostile(FakeRunner):
        async def start(self, app_id, app_dir, env):
            if app_id == "a_os":
                raise OSError("env file unwritable")
            if app_id == "b_bare":
                raise KeyError("surprise")
            await super().start(app_id, app_dir, env)

    for n in ("a_os", "b_bare", "c_ok", "d_ok"):
        _mk_app(tmp_path, n)
    sup = AppSupervisor(apps_root=tmp_path, runner=Hostile())
    with caplog.at_level("ERROR"):
        asyncio.run(sup.discover_and_start())
    assert {"c_ok", "d_ok"} <= set(sup.running_apps)
    assert sup.failed_apps == {"a_os", "b_bare"}
    assert "env file unwritable" in caplog.text and "surprise" in caplog.text


def test_concurrent_starts_of_one_app_are_serialized(tmp_path):
    from main import AppSupervisor
    _mk_app(tmp_path, "shop")

    class Slow(FakeRunner):
        active = 0
        peak = 0

        async def start(self, app_id, app_dir, env):
            Slow.active += 1
            Slow.peak = max(Slow.peak, Slow.active)
            await asyncio.sleep(0.05)
            Slow.active -= 1
            await super().start(app_id, app_dir, env)

    sup = AppSupervisor(apps_root=tmp_path, runner=Slow())

    async def go():
        await asyncio.gather(sup.start_app("shop"), sup.start_app("shop"))

    asyncio.run(go())
    assert Slow.peak == 1


def test_start_and_stop_of_one_app_are_serialized(tmp_path):
    from main import AppSupervisor
    _mk_app(tmp_path, "shop")
    order = []

    class Slow(FakeRunner):
        async def start(self, app_id, app_dir, env):
            order.append("start-begin")
            await asyncio.sleep(0.05)
            order.append("start-end")
            await super().start(app_id, app_dir, env)

        async def stop(self, app_id):
            order.append("stop")
            await super().stop(app_id)

    sup = AppSupervisor(apps_root=tmp_path, runner=Slow())

    async def go():
        await sup.start_app("shop")
        order.clear()
        t = asyncio.create_task(sup.start_app("shop"))  # a reload...
        await asyncio.sleep(0.01)
        await sup.stop_app("shop")                      # ...racing a stop
        await t

    asyncio.run(go())
    assert order == ["start-begin", "start-end", "stop"]


class _ProdRunner(FakeRunner):
    isolated = True


def test_isolating_runner_without_provisioner_is_refused(tmp_path):
    from main import AppSupervisor
    with pytest.raises(ValueError, match="db_url_for_app"):
        AppSupervisor(apps_root=tmp_path, runner=_ProdRunner())
    AppSupervisor(apps_root=tmp_path, runner=_ProdRunner(), db_url_for_app=_fake_provision)
    AppSupervisor(apps_root=tmp_path, runner=FakeRunner())


def test_identity_vars_follow_the_runners_isolated_flag(tmp_path):
    from main import AppSupervisor
    _mk_app(tmp_path, "shop")
    runner = _ProdRunner()
    sup = AppSupervisor(apps_root=tmp_path, runner=runner, db_url_for_app=_fake_provision)
    with patch.dict(os.environ, {"HOME": "/home/ivan", "USER": "ivan"}, clear=False):
        asyncio.run(sup.start_app("shop"))
    assert "HOME" not in runner.started["shop"] and "USER" not in runner.started["shop"]


def test_start_app_returns_whether_it_started(tmp_path):
    from main import AppSupervisor

    class Bad(FakeRunner):
        async def start(self, app_id, app_dir, env):
            raise RuntimeError("nope")

    _mk_app(tmp_path, "shop")
    assert asyncio.run(AppSupervisor(apps_root=tmp_path, runner=FakeRunner()).start_app("shop")) is True
    assert asyncio.run(AppSupervisor(apps_root=tmp_path, runner=Bad()).start_app("shop")) is False


def test_failed_start_is_retried_by_the_watchdog_until_it_works(tmp_path):
    from main import AppSupervisor

    class Flaky(FakeRunner):
        fail = True

        async def start(self, app_id, app_dir, env):
            if Flaky.fail:
                raise RuntimeError("not yet")
            await super().start(app_id, app_dir, env)

    _mk_app(tmp_path, "shop")
    sup = AppSupervisor(apps_root=tmp_path, runner=Flaky())

    async def go():
        await sup.start_app("shop")
        assert sup.failed_apps == {"shop"} and "shop" not in sup.running_apps
        await sup._watch_tick()
        assert sup.failed_apps == {"shop"}
        Flaky.fail = False
        await sup._watch_tick()

    asyncio.run(go())
    assert sup.failed_apps == set() and "shop" in sup.running_apps


def test_failed_apps_do_not_block_other_retries_when_one_raises(tmp_path):
    from main import AppSupervisor
    for n in ("a", "b"):
        _mk_app(tmp_path, n)
    runner = FakeRunner()
    sup = AppSupervisor(apps_root=tmp_path, runner=runner)
    sup.failed_apps = {"a", "b"}
    original = sup.start_app

    async def flaky(app_id):
        if app_id == "a":
            raise KeyError("x")
        return await original(app_id)

    sup.start_app = flaky
    asyncio.run(sup._watch_tick())
    assert "b" in runner.started and "a" in sup.failed_apps


def _client(monkeypatch, sup):
    import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "app_supervisor", sup)
    monkeypatch.setenv("CONTROLLER_API_TOKEN", "t")
    return TestClient(main.app), {"Authorization": "Bearer t"}


def test_reload_returns_502_when_the_app_fails_to_start(tmp_path, monkeypatch):
    from main import AppSupervisor

    class Bad(FakeRunner):
        async def start(self, app_id, app_dir, env):
            raise RuntimeError("nope")

    _mk_app(tmp_path, "shop")
    client, h = _client(monkeypatch, AppSupervisor(apps_root=tmp_path, runner=Bad()))
    r = client.post("/apps/reload", json={"app_id": "shop"}, headers=h)
    assert r.status_code == 502
    assert "shop" in r.json()["detail"]


def test_reload_succeeds_for_a_good_app(tmp_path, monkeypatch):
    from main import AppSupervisor
    _mk_app(tmp_path, "shop")
    client, h = _client(monkeypatch, AppSupervisor(apps_root=tmp_path, runner=FakeRunner()))
    assert client.post("/apps/reload", json={"app_id": "shop"}, headers=h).status_code == 200


@pytest.mark.parametrize("bad", ["My-App", "my-app", "UPPER", "x" * 60, ""])
def test_reload_rejects_ids_the_db_role_rule_rejects(tmp_path, monkeypatch, bad):
    from main import AppSupervisor
    client, h = _client(monkeypatch, AppSupervisor(apps_root=tmp_path, runner=FakeRunner()))
    assert client.post("/apps/reload", json={"app_id": bad}, headers=h).status_code == 400


def test_list_apps_reports_status_and_includes_failed(tmp_path, monkeypatch):
    from main import AppSupervisor

    class Picky(FakeRunner):
        async def start(self, app_id, app_dir, env):
            if app_id == "bad":
                raise RuntimeError("nope")
            await super().start(app_id, app_dir, env)

    for n in ("good", "bad"):
        _mk_app(tmp_path, n)
    sup = AppSupervisor(apps_root=tmp_path, runner=Picky())
    asyncio.run(sup.discover_and_start())
    client, _ = _client(monkeypatch, sup)
    body = client.get("/apps").json()
    assert body["good"]["status"] == "running" and body["good"]["pid"] == 4242
    assert body["bad"]["status"] == "failed" and body["bad"]["pid"] is None


def test_apps_root_check_fails_closed_for_isolated_runner(tmp_path):
    from main import _check_apps_root
    (tmp_path / "apps").mkdir()
    _check_apps_root(_ProdRunner(), tmp_path / "apps", tmp_path / "apps")
    _check_apps_root(FakeRunner(), tmp_path / "elsewhere", tmp_path / "apps")  # dev: any root
    with pytest.raises(RuntimeError, match="apps"):
        _check_apps_root(_ProdRunner(), tmp_path / "elsewhere", tmp_path / "apps")


def _cfg_for(**kw):
    from config import Config
    return Config(_env_file=None, **kw)


def test_build_supervisor_systemd_gets_systemd_runner_and_provisioner(tmp_path):
    from main import _build_supervisor, _provision_app_db
    from app_runner import SystemdRunner
    cfg = _cfg_for(app_runner="systemd", app_db_secret="s" * 40, app_env_dir=str(tmp_path))
    sup = _build_supervisor(cfg, apps_root=tmp_path, repo_apps_root=tmp_path)
    assert isinstance(sup.runner, SystemdRunner) and sup.db_url_for_app is _provision_app_db


def test_build_supervisor_subprocess_with_secret_gets_provisioner(tmp_path):
    from main import _build_supervisor, _provision_app_db
    from app_runner import SubprocessRunner
    sup = _build_supervisor(_cfg_for(app_db_secret="s" * 40), apps_root=tmp_path, repo_apps_root=tmp_path)
    assert isinstance(sup.runner, SubprocessRunner) and sup.db_url_for_app is _provision_app_db


def test_build_supervisor_subprocess_without_secret_has_no_provisioner(tmp_path):
    from main import _build_supervisor
    sup = _build_supervisor(_cfg_for(), apps_root=tmp_path, repo_apps_root=tmp_path)
    assert sup.db_url_for_app is None


def test_build_supervisor_systemd_rejects_foreign_apps_root(tmp_path):
    from main import _build_supervisor
    cfg = _cfg_for(app_runner="systemd", app_db_secret="s" * 40, app_env_dir=str(tmp_path))
    with pytest.raises(RuntimeError):
        _build_supervisor(cfg, apps_root=tmp_path / "x", repo_apps_root=tmp_path / "apps")

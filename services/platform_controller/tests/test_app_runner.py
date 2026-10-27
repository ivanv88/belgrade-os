from __future__ import annotations
import asyncio
import os
import signal
import stat
import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from app_runner import SubprocessRunner, SystemdRunner


class FakeRun:
    """Stands in for subprocess.run; records argv, returns canned results."""
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "", timeout: bool = False):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.returncode, self.stdout, self.stderr, self.timeout = returncode, stdout, stderr, timeout

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        self.kwargs.append(kwargs)
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))
        if kwargs.get("check") and self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, argv, self.stdout, self.stderr)
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


SC = ["systemctl", "--no-ask-password"]
UNIT = "belgrade-os-app@shopping.service"


def test_systemd_start_writes_private_env_file_then_restarts_unit(tmp_path):
    run = FakeRun()
    runner = SystemdRunner(env_dir=tmp_path, run=run)

    asyncio.run(runner.start("shopping", tmp_path, {"BEG_OS_PORT": "9002", "PATH": "/usr/bin"}))

    env_file = tmp_path / "shopping.env"
    assert env_file.read_text() == 'BEG_OS_PORT="9002"\nPATH="/usr/bin"\n'
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert run.calls == [SC + ["restart", UNIT]]


def test_systemd_env_file_rejects_values_that_could_inject_lines(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun())
    with pytest.raises(ValueError):
        asyncio.run(runner.start("shopping", tmp_path, {"X": 'a"\nEVIL=1'}))


@pytest.mark.parametrize("key,value", [
    ("BAD-KEY", "v"), ("1BAD", "v"), ("", "v"), ("A B", "v"),
    ("K", "a\nb"), ("K", "a\rb"), ("K", 'a"b'), ("K", "a\\b"), ("K", "abc\\"),
])
def test_systemd_rejects_unsafe_entries(tmp_path, key, value):
    run = FakeRun()
    runner = SystemdRunner(env_dir=tmp_path, run=run)
    with pytest.raises(ValueError):
        asyncio.run(runner.start("shopping", tmp_path, {"OK": "1", key: value}))
    assert run.calls == []


def test_systemd_accepts_database_url_with_percent_and_at(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun())
    url = "postgresql://u:p%40ss@localhost:5432/db?x=$y"
    asyncio.run(runner.start("shopping", tmp_path, {"BEG_OS_DB_URL": url}))
    assert (tmp_path / "shopping.env").read_text() == f'BEG_OS_DB_URL="{url}"\n'


def test_systemd_rejected_entry_leaves_existing_file_untouched(tmp_path):
    env_file = tmp_path / "shopping.env"
    env_file.write_text("OLD\n")
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun())
    with pytest.raises(ValueError):
        asyncio.run(runner.start("shopping", tmp_path, {"A": "1", "B": "x\ny"}))
    assert env_file.read_text() == "OLD\n"
    assert [p.name for p in tmp_path.iterdir()] == ["shopping.env"]


def test_systemd_replaces_stale_world_readable_env_file_privately(tmp_path):
    env_file = tmp_path / "shopping.env"
    env_file.write_text("STALE=1\n")
    env_file.chmod(0o644)
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun())

    asyncio.run(runner.start("shopping", tmp_path, {"A": "1"}))

    assert env_file.read_text() == 'A="1"\n'
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["shopping.env"]


def test_systemd_start_raises_when_restart_fails(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun(returncode=1))
    with pytest.raises(RuntimeError):
        asyncio.run(runner.start("shopping", tmp_path, {"A": "1"}))


def test_systemd_rejects_unsafe_app_id(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun())
    with pytest.raises(ValueError):
        asyncio.run(runner.start("x.service other", tmp_path, {}))


def test_systemd_stop_and_status(tmp_path):
    class Run(FakeRun):
        def __call__(self, argv, **kwargs):
            r = super().__call__(argv, **kwargs)
            r.stdout = "active\n" if "is-active" in argv else "1234\n"
            return r

    run = Run(returncode=0)
    runner = SystemdRunner(env_dir=tmp_path, run=run)

    assert runner.is_running("shopping") is True
    assert runner.pid("shopping") == 1234
    asyncio.run(runner.stop("shopping"))

    assert SC + ["is-active", UNIT] in run.calls
    assert SC + ["stop", UNIT] in run.calls


def test_systemd_reports_stopped_unit(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun(returncode=3, stdout="inactive\n"))
    assert runner.is_running("shopping") is False
    assert runner.pid("shopping") is None


@pytest.mark.parametrize("state,expected", [
    ("active", True), ("activating", True), ("reloading", True),
    ("deactivating", False), ("failed", False), ("inactive", False), ("", False),
])
def test_systemd_is_running_by_unit_state(tmp_path, state, expected):
    """A unit in systemd's own Restart= backoff reports 'activating'; the watchdog must not pile a restart on top."""
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun(returncode=0 if state == "active" else 3, stdout=state + "\n"))
    assert runner.is_running("shopping") is expected


def test_subprocess_runner_spawns_and_tracks(tmp_path):
    proc = MagicMock(pid=42)
    proc.poll.return_value = None
    runner = SubprocessRunner()
    with patch("app_runner.subprocess.Popen", return_value=proc) as popen, \
         patch("app_runner.open", MagicMock(), create=True):
        asyncio.run(runner.start("shopping", tmp_path, {"A": "1"}))

    assert popen.call_args.kwargs["env"] == {"A": "1"}
    assert popen.call_args.args[0][1] == str(tmp_path / "main.py")
    assert runner.is_running("shopping") and runner.pid("shopping") == 42
    proc.poll.return_value = 1
    assert runner.is_running("shopping") is False


def test_systemd_start_failure_keeps_stderr(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun(returncode=1, stderr="Access denied\n"))
    with pytest.raises(RuntimeError, match="systemctl restart .* failed: Access denied$") as ei:
        asyncio.run(runner.start("shopping", tmp_path, {"A": "1"}))
    assert isinstance(ei.value.__cause__, subprocess.CalledProcessError)


def test_systemd_start_failure_with_empty_stderr(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun(returncode=1))
    with pytest.raises(RuntimeError, match="failed"):
        asyncio.run(runner.start("shopping", tmp_path, {}))


def test_systemd_stop_unit_not_loaded_is_success(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun(returncode=5, stderr="not loaded"))
    asyncio.run(runner.stop("shopping"))


def test_systemd_stop_failure_raises_with_stderr(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun(returncode=1, stderr="Interactive authentication required"))
    with pytest.raises(RuntimeError, match="Interactive authentication required"):
        asyncio.run(runner.stop("shopping"))


def test_systemd_all_calls_are_non_interactive_and_bounded(tmp_path):
    run = FakeRun(stdout="7\n")
    runner = SystemdRunner(env_dir=tmp_path, run=run)
    asyncio.run(runner.start("shopping", tmp_path, {}))
    asyncio.run(runner.stop("shopping"))
    runner.is_running("shopping")
    runner.pid("shopping")
    assert len(run.calls) == 4
    for argv, kw in zip(run.calls, run.kwargs):
        assert argv[:2] == SC
        assert kw["timeout"] == 10


def test_systemd_timeouts(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun(timeout=True))
    assert runner.is_running("shopping") is False
    assert runner.pid("shopping") is None
    with pytest.raises(RuntimeError):
        asyncio.run(runner.start("shopping", tmp_path, {}))
    with pytest.raises(RuntimeError):
        asyncio.run(runner.stop("shopping"))


def test_systemd_missing_env_dir_is_a_clear_error(tmp_path):
    missing = tmp_path / "nope"
    runner = SystemdRunner(env_dir=missing, run=FakeRun())
    with pytest.raises(RuntimeError, match="RuntimeDirectory=belgrade-os/apps") as ei:
        asyncio.run(runner.start("shopping", tmp_path, {}))
    assert str(missing) in str(ei.value)


def _write_app(app_dir, ignore_term: bool):
    ready = app_dir / "ready"
    (app_dir / "main.py").write_text(
        "import signal, time, pathlib\n"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
        + f"pathlib.Path({str(ready)!r}).write_text('1')\n"
        "time.sleep(15)\n"
    )
    return ready


def _wait_for(path):
    deadline = time.time() + 10
    while not path.exists() and time.time() < deadline:
        time.sleep(0.02)
    assert path.exists()


@pytest.mark.parametrize("ignore_term", [False, True])
def test_subprocess_runner_really_stops_real_process(tmp_path, ignore_term, caplog):
    ready = _write_app(tmp_path, ignore_term)
    runner = SubprocessRunner()
    runner.stop_timeout_s = 0.5

    started = []

    async def scenario():
        await runner.start("shopping", tmp_path, {"PATH": os.environ["PATH"]})
        proc = runner._procs["shopping"]
        started.append(proc)
        await asyncio.to_thread(_wait_for, ready)
        assert runner.is_running("shopping")
        await runner.stop("shopping")
        return proc

    try:
        with caplog.at_level("WARNING", logger="app_runner"):
            proc = asyncio.run(scenario())
    finally:
        for p in started:  # never orphan the child if an assertion above failed
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert ("SIGKILL" in caplog.text) is ignore_term
    assert proc.poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)
    assert not runner.is_running("shopping") and runner.pid("shopping") is None


def test_subprocess_runner_pid_none_after_exit(tmp_path):
    proc = MagicMock(pid=42)
    proc.poll.return_value = 0
    runner = SubprocessRunner()
    runner._procs["shopping"] = proc
    assert runner.pid("shopping") is None


def test_subprocess_runner_closes_log_handle_when_popen_fails(tmp_path):
    runner = SubprocessRunner()
    log = MagicMock()
    with patch("app_runner.open", MagicMock(return_value=log), create=True), \
         patch("app_runner.subprocess.Popen", side_effect=OSError("boom")):
        with pytest.raises(OSError):
            asyncio.run(runner.start("shopping", tmp_path, {}))
    log.__exit__.assert_called_once()
    assert "shopping" not in runner._procs


def test_subprocess_runner_stop_logs_unexpected_errors(tmp_path, caplog):
    proc = MagicMock(pid=42)
    runner = SubprocessRunner()
    runner._procs["shopping"] = proc
    with patch("app_runner.os.killpg", side_effect=PermissionError("nope")), \
         caplog.at_level("ERROR", logger="app_runner"):
        asyncio.run(runner.stop("shopping"))
    assert "nope" in caplog.text

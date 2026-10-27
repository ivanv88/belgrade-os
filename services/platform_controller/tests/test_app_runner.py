from __future__ import annotations
import asyncio
import stat
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app_runner import SubprocessRunner, SystemdRunner


class FakeRun:
    """Stands in for subprocess.run; records argv, returns canned results."""
    def __init__(self, returncode: int = 0, stdout: str = ""):
        self.calls: list[list[str]] = []
        self.returncode, self.stdout = returncode, stdout

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if kwargs.get("check") and self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, argv, self.stdout, "")
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_systemd_start_writes_private_env_file_then_restarts_unit(tmp_path):
    run = FakeRun()
    runner = SystemdRunner(env_dir=tmp_path, run=run)

    asyncio.run(runner.start("shopping", tmp_path, {"BEG_OS_PORT": "9002", "PATH": "/usr/bin"}))

    env_file = tmp_path / "shopping.env"
    assert env_file.read_text() == 'BEG_OS_PORT="9002"\nPATH="/usr/bin"\n'
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert run.calls == [["systemctl", "restart", "belgrade-os-app@shopping.service"]]


def test_systemd_env_file_rejects_values_that_could_inject_lines(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun())
    with pytest.raises(ValueError):
        asyncio.run(runner.start("shopping", tmp_path, {"X": 'a"\nEVIL=1'}))


@pytest.mark.parametrize("key,value", [
    ("BAD-KEY", "v"), ("1BAD", "v"), ("", "v"), ("A B", "v"),
    ("K", "a\nb"), ("K", "a\rb"), ("K", 'a"b'), ("K", "a\\b"),
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
    with pytest.raises(subprocess.CalledProcessError):
        asyncio.run(runner.start("shopping", tmp_path, {"A": "1"}))


def test_systemd_rejects_unsafe_app_id(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun())
    with pytest.raises(ValueError):
        asyncio.run(runner.start("x.service other", tmp_path, {}))


def test_systemd_stop_and_status(tmp_path):
    run = FakeRun(returncode=0, stdout="1234\n")
    runner = SystemdRunner(env_dir=tmp_path, run=run)

    assert runner.is_running("shopping") is True
    assert runner.pid("shopping") == 1234
    asyncio.run(runner.stop("shopping"))

    assert ["systemctl", "is-active", "--quiet", "belgrade-os-app@shopping.service"] in run.calls
    assert ["systemctl", "stop", "belgrade-os-app@shopping.service"] in run.calls


def test_systemd_reports_stopped_unit(tmp_path):
    runner = SystemdRunner(env_dir=tmp_path, run=FakeRun(returncode=3, stdout="0\n"))
    assert runner.is_running("shopping") is False
    assert runner.pid("shopping") is None


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

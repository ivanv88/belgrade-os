import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from ephemeral_runner import EphemeralRunner, OutputValidationError


SECCOMP = "/config/seccomp-untrusted.json"


def make_runner(tmp_path):
    return EphemeralRunner(seccomp_profile=SECCOMP, apps_root=tmp_path)


def _make_proc(returncode=0, stdout=b"", stderr=b""):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


def _write_output(args, content: str):
    """Extract work_dir from docker run args and write output.json."""
    for arg in args:
        if ":/workspace" in str(arg):
            work_dir = Path(str(arg).split(":/workspace")[0])
            (work_dir / "output.json").write_text(content)
            return
    raise AssertionError("volume mount arg not found in: " + str(args))


@pytest.mark.asyncio
async def test_run_success(tmp_path):
    runner = make_runner(tmp_path)
    output = {"result": "ok", "data": 42}

    async def fake_subprocess(*args, **kwargs):
        _write_output(args, json.dumps(output))
        return _make_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        result = await runner.run("task-1", "myapp", {"tool_name": "do_thing"})

    assert result == output


@pytest.mark.asyncio
async def test_run_timeout(tmp_path):
    runner = make_runner(tmp_path)
    subprocess_calls: list[tuple] = []

    async def dispatch(*args, **kwargs):
        subprocess_calls.append(args)
        if args[1] == "run":
            # Simulate a hanging docker run process.
            # kill() must be a regular (sync) function — asyncio.subprocess.Process.kill is sync.
            proc = MagicMock()
            proc.kill = MagicMock()
            proc.wait = AsyncMock(return_value=None)
            async def hang():
                await asyncio.sleep(9999)
                return b"", b""
            proc.communicate = hang
            return proc
        else:
            # docker rm -f call — return immediately
            rm = MagicMock()
            rm.wait = AsyncMock(return_value=None)
            return rm

    with patch("asyncio.create_subprocess_exec", side_effect=dispatch):
        with patch("ephemeral_runner._TIMEOUT_S", 0.01):
            with pytest.raises(asyncio.TimeoutError):
                await runner.run("task-timeout", "myapp", {})

    rm_calls = [c for c in subprocess_calls if len(c) >= 3 and c[1] == "rm"]
    assert len(rm_calls) == 1, f"expected one docker rm call, got: {subprocess_calls}"
    assert "beg-task-timeout" in rm_calls[0], f"expected container name in rm args: {rm_calls[0]}"


@pytest.mark.asyncio
async def test_container_nonzero_exit_raises(tmp_path):
    runner = make_runner(tmp_path)

    with patch("asyncio.create_subprocess_exec", return_value=_make_proc(returncode=1, stderr=b"crash")):
        with pytest.raises(RuntimeError, match="Container exited 1"):
            await runner.run("task-fail", "myapp", {})


@pytest.mark.asyncio
async def test_output_missing_raises(tmp_path):
    runner = make_runner(tmp_path)

    with patch("asyncio.create_subprocess_exec", return_value=_make_proc(returncode=0)):
        with pytest.raises(OutputValidationError, match="output.json not created"):
            await runner.run("task-no-output", "myapp", {})


@pytest.mark.asyncio
async def test_output_too_large_raises(tmp_path):
    runner = make_runner(tmp_path)

    async def write_large(*args, **kwargs):
        _write_output(args, "x" * 1_100_000)
        return _make_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=write_large):
        with pytest.raises(OutputValidationError, match="too large"):
            await runner.run("task-large", "myapp", {})


@pytest.mark.asyncio
async def test_output_invalid_json_raises(tmp_path):
    runner = make_runner(tmp_path)

    async def write_bad(*args, **kwargs):
        _write_output(args, "not json!!!")
        return _make_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=write_bad):
        with pytest.raises(OutputValidationError, match="not valid JSON"):
            await runner.run("task-bad-json", "myapp", {})


@pytest.mark.asyncio
async def test_work_dir_cleaned_up_on_success(tmp_path):
    runner = make_runner(tmp_path)
    seen: list[Path] = []

    async def fake_subprocess(*args, **kwargs):
        for arg in args:
            if ":/workspace" in str(arg):
                wd = Path(str(arg).split(":/workspace")[0])
                seen.append(wd)
                (wd / "output.json").write_text('{"ok": true}')
                break
        return _make_proc(returncode=0)

    with patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess):
        await runner.run("task-cleanup-ok", "myapp", {})

    assert len(seen) == 1
    assert not seen[0].exists(), "work dir must be removed after success"


@pytest.mark.asyncio
async def test_work_dir_cleaned_up_on_failure(tmp_path):
    runner = make_runner(tmp_path)
    seen: list[Path] = []

    async def fail_proc(*args, **kwargs):
        for arg in args:
            if ":/workspace" in str(arg):
                seen.append(Path(str(arg).split(":/workspace")[0]))
                break
        return _make_proc(returncode=1, stderr=b"crash")

    with patch("asyncio.create_subprocess_exec", side_effect=fail_proc):
        with pytest.raises(RuntimeError):
            await runner.run("task-cleanup-fail", "myapp", {})

    assert len(seen) == 1
    assert not seen[0].exists(), "work dir must be removed even on failure"

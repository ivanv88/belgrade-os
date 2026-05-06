from __future__ import annotations
import sys
import pytest
from unittest.mock import patch, MagicMock, call
import importlib.util
import pathlib

# Import watchdog without executing main()
spec = importlib.util.spec_from_file_location(
    "watchdog", pathlib.Path(__file__).parent.parent / "watchdog.py"
)
watchdog = importlib.util.module_from_spec(spec)
sys.modules["watchdog"] = watchdog
spec.loader.exec_module(watchdog)


def test_read_temps_returns_list_of_ints(tmp_path):
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "temp").write_text("55000\n")

    with patch("glob.glob", return_value=[str(zone / "temp")]):
        temps = watchdog.read_temps()

    assert temps == [55000]


def test_read_temps_skips_unreadable_files(tmp_path):
    with patch("glob.glob", return_value=["/nonexistent/path/temp"]):
        temps = watchdog.read_temps()
    assert temps == []


def test_max_temp_returns_peak():
    assert watchdog.max_temp([50000, 72000, 68000]) == 72000


def test_max_temp_returns_zero_on_empty():
    assert watchdog.max_temp([]) == 0


def test_warn_threshold_triggers_ntfy(capsys):
    with patch("watchdog.notify_ntfy") as mock_notify, \
         patch("watchdog.read_temps", side_effect=[[80500], [80500], StopIteration()]), \
         patch("time.sleep"):
        try:
            watchdog.main()
        except StopIteration:
            pass
    mock_notify.assert_called()
    first_call_msg = mock_notify.call_args_list[0].args[0]
    assert "80" in first_call_msg


def test_emergency_threshold_calls_compose_down():
    with patch("watchdog.notify_ntfy"), \
         patch("subprocess.run") as mock_run, \
         patch("watchdog.read_temps", return_value=[91000]), \
         patch("time.sleep"), \
         pytest.raises(SystemExit):
        watchdog.main()

    calls = [str(c) for c in mock_run.call_args_list]
    assert any("compose" in c and "down" in c for c in calls)

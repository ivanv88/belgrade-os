import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from core.executor import CircuitBreaker, safe_execute, reset_breaker


def test_circuit_breaker_opens_after_threshold() -> None:
    breaker = CircuitBreaker("test-app", threshold=3, window_seconds=600)
    assert not breaker.is_open()
    breaker.record_failure()
    breaker.record_failure()
    opened = breaker.record_failure()
    assert opened is True
    assert breaker.is_open()


def test_circuit_breaker_resets() -> None:
    breaker = CircuitBreaker("test-app", threshold=3, window_seconds=600)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.is_open()
    breaker.reset()
    assert not breaker.is_open()


def test_circuit_breaker_expires_old_failures() -> None:
    breaker = CircuitBreaker("test-app", threshold=3, window_seconds=1)
    breaker.record_failure()
    breaker.record_failure()
    # Simulate old failures by backdating them
    breaker._failures[0] = time.time() - 2
    breaker._failures[1] = time.time() - 2
    opened = breaker.record_failure()
    # Only 1 failure in window — should not open
    assert opened is False


async def test_safe_execute_calls_execute(tmp_path) -> None:
    mock_module = MagicMock()
    mock_module.execute = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.cleanup = AsyncMock()
    bus = MagicMock()
    bus.emit = AsyncMock()

    with patch("core.executor.lazy_load", return_value=mock_module):
        reset_breaker("myapp")
        await safe_execute("myapp", mock_ctx, bus)

    mock_module.execute.assert_called_once_with(mock_ctx)
    mock_ctx.cleanup.assert_called_once()


async def test_safe_execute_calls_cleanup_on_error(tmp_path) -> None:
    mock_module = MagicMock()
    mock_module.execute = AsyncMock(side_effect=ValueError("boom"))
    mock_ctx = AsyncMock()
    mock_ctx.cleanup = AsyncMock()
    mock_ctx.notify = AsyncMock()
    mock_ctx.notify.send = AsyncMock()
    bus = MagicMock()
    bus.emit = AsyncMock()

    with patch("core.executor.lazy_load", return_value=mock_module):
        reset_breaker("myapp")
        await safe_execute("myapp", mock_ctx, bus)

    mock_ctx.cleanup.assert_called_once()


async def test_safe_execute_skips_open_circuit() -> None:
    mock_module = MagicMock()
    mock_module.execute = AsyncMock()
    mock_ctx = AsyncMock()
    bus = MagicMock()
    bus.emit = AsyncMock()

    reset_breaker("blocked-app")
    from core.executor import _circuit_breakers
    breaker = CircuitBreaker("blocked-app", threshold=1)
    breaker.record_failure()
    _circuit_breakers["blocked-app"] = breaker

    with patch("core.executor.lazy_load", return_value=mock_module):
        await safe_execute("blocked-app", mock_ctx, bus)

    mock_module.execute.assert_not_called()

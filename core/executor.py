from __future__ import annotations
import asyncio
import importlib
import logging
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)
_semaphore = asyncio.Semaphore(3)
_circuit_breakers: Dict[str, CircuitBreaker] = {}


class CircuitBreaker:
    def __init__(
        self, app_id: str, threshold: int = 3, window_seconds: int = 600
    ) -> None:
        self.app_id = app_id
        self.threshold = threshold
        self.window_seconds = window_seconds
        self._failures: deque[float] = deque()
        self._open = False

    def record_failure(self) -> bool:
        now = time.time()
        while self._failures and now - self._failures[0] > self.window_seconds:
            self._failures.popleft()
        self._failures.append(now)
        if len(self._failures) >= self.threshold:
            self._open = True
            return True
        return False

    def is_open(self) -> bool:
        return self._open

    def reset(self) -> None:
        self._open = False
        self._failures.clear()


def reset_breaker(app_id: str) -> None:
    if app_id in _circuit_breakers:
        _circuit_breakers[app_id].reset()
    else:
        _circuit_breakers[app_id] = CircuitBreaker(app_id)


async def lazy_load(app_id: str) -> Any:
    return importlib.import_module(f"apps.{app_id}.main")


async def safe_execute(app_id: str, ctx: Any, event_bus: Any, **kwargs: Any) -> None:
    breaker = _circuit_breakers.setdefault(app_id, CircuitBreaker(app_id))
    if breaker.is_open():
        logger.warning("Circuit open for %s — skipping", app_id)
        return

    async with _semaphore:
        try:
            module = await lazy_load(app_id)
            await module.execute(ctx, **kwargs)
        except Exception as e:
            logger.error("App %s failed: %s", app_id, e, exc_info=True)
            broken = breaker.record_failure()
            if broken:
                await event_bus.emit(
                    "system.circuit_broken",
                    {"app_id": app_id, "failure_count": breaker.threshold},
                )
                await ctx.notify.send(
                    f"Circuit broken: {app_id}", title="Belgrade OS", priority="high"
                )
            await event_bus.emit(
                "system.app_failed",
                {"app_id": app_id, "error": str(e), "timestamp": datetime.now()},
            )
        finally:
            await ctx.cleanup()

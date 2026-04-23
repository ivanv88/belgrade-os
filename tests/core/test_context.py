import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime
from pathlib import Path
from core.context import AppContext, AppMeta
from core.models.user import User
from core.io import LocalAdapter
from shared.ntfy import NotifyService
from core.eventbus import EventBus


def make_user() -> User:
    return User(name="Laurent", email="laurent@example.com")


def make_meta(app_id: str = "test-app") -> AppMeta:
    return AppMeta(app_id=app_id, timestamp=datetime.now(), secrets={})


async def test_context_exposes_all_properties(tmp_dir: Path) -> None:
    user = make_user()
    meta = make_meta()
    io = LocalAdapter(tmp_dir)
    notify = NotifyService(topic="test")
    bus = EventBus()
    db_session = AsyncMock()
    metrics: dict = {"weight_kg": 104}

    ctx = AppContext(
        user=user,
        metrics=metrics,
        db=db_session,
        io=io,
        event_bus=bus,
        notify=notify,
        meta=meta,
    )

    assert ctx.user.name == "Laurent"
    assert ctx.metrics == {"weight_kg": 104}
    assert ctx.meta.app_id == "test-app"


async def test_ctx_emit_delegates_to_eventbus(tmp_dir: Path) -> None:
    bus = EventBus()
    bus.emit = AsyncMock()
    ctx = AppContext(
        user=make_user(), metrics={}, db=AsyncMock(),
        io=LocalAdapter(tmp_dir), event_bus=bus,
        notify=NotifyService(topic="t"), meta=make_meta(),
    )
    await ctx.emit("myapp.event", {"key": "value"})
    bus.emit.assert_called_once_with("myapp.event", {"key": "value"})


async def test_ctx_cleanup_rolls_back_db(tmp_dir: Path) -> None:
    db_session = AsyncMock()
    ctx = AppContext(
        user=make_user(), metrics={}, db=db_session,
        io=LocalAdapter(tmp_dir), event_bus=EventBus(),
        notify=NotifyService(topic="t"), meta=make_meta(),
    )
    await ctx.cleanup()
    db_session.rollback.assert_called_once()
    db_session.close.assert_called_once()


async def test_ctx_cleanup_survives_db_failure(tmp_dir: Path) -> None:
    db_session = AsyncMock()
    db_session.rollback.side_effect = Exception("connection lost")
    db_session.close.side_effect = Exception("connection lost")
    ctx = AppContext(
        user=make_user(), metrics={}, db=db_session,
        io=LocalAdapter(tmp_dir), event_bus=EventBus(),
        notify=NotifyService(topic="t"), meta=make_meta(),
    )
    # Should not raise even when both DB calls fail
    await ctx.cleanup()

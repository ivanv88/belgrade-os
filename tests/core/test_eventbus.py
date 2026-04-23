import pytest
from datetime import datetime
from pydantic import BaseModel
from core.eventbus import EventBus
from core.events import SystemAppFailed, SystemCircuitBroken


class GoalReached(BaseModel):
    calories: int
    deficit_met: bool


async def test_register_and_emit_valid_schema() -> None:
    bus = EventBus()
    bus.register_schema("nutrition.goal_reached", GoalReached)
    await bus.emit("nutrition.goal_reached", {"calories": 2000, "deficit_met": True})
    topic, event = await bus.get()
    assert topic == "nutrition.goal_reached"
    assert isinstance(event, GoalReached)
    assert event.calories == 2000


async def test_emit_invalid_payload_raises() -> None:
    bus = EventBus()
    bus.register_schema("nutrition.goal_reached", GoalReached)
    with pytest.raises(Exception):
        await bus.emit("nutrition.goal_reached", {"calories": "not_an_int"})


async def test_emit_unknown_system_topic_raises() -> None:
    bus = EventBus()
    with pytest.raises(ValueError, match="system"):
        await bus.emit("system.unknown_event", {"foo": "bar"})


async def test_emit_unknown_app_topic_passes_through() -> None:
    bus = EventBus()
    await bus.emit("myapp.something", {"any": "data"})
    topic, event = await bus.get()
    assert topic == "myapp.something"
    assert event == {"any": "data"}


async def test_register_subscription() -> None:
    bus = EventBus()
    bus.register_subscription("nutrition.goal_reached", "wiki-compiler")
    subscribers = bus.get_subscribers("nutrition.goal_reached")
    assert "wiki-compiler" in subscribers


async def test_system_app_failed_schema() -> None:
    event = SystemAppFailed(app_id="nutrition", error="KeyError", timestamp=datetime.now())
    assert event.app_id == "nutrition"


async def test_system_circuit_broken_schema() -> None:
    event = SystemCircuitBroken(app_id="nutrition", failure_count=3)
    assert event.failure_count == 3

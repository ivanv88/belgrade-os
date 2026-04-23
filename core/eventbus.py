from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict, List, Type
from pydantic import BaseModel
from core.events import SYSTEM_SCHEMAS

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._schemas: Dict[str, Type[BaseModel]] = dict(SYSTEM_SCHEMAS)
        self._subscriptions: Dict[str, List[str]] = {}
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def register_schema(self, topic: str, schema: Type[BaseModel]) -> None:
        self._schemas[topic] = schema

    def register_subscription(self, topic: str, app_id: str) -> None:
        self._subscriptions.setdefault(topic, []).append(app_id)

    def get_subscribers(self, topic: str) -> List[str]:
        return self._subscriptions.get(topic, [])

    async def emit(self, topic: str, data: Any) -> None:
        if isinstance(data, dict):
            if topic in self._schemas:
                data = self._schemas[topic].model_validate(data)
            elif topic.startswith("system."):
                raise ValueError(f"No schema registered for system topic: {topic}")
            else:
                logger.warning("No schema for topic '%s' — passing dict through", topic)
        await self._queue.put((topic, data))

    async def get(self) -> tuple[str, Any]:
        return await self._queue.get()

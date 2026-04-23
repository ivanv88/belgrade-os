from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession
from core.io import IOAdapter
from core.models.user import User
from shared.ntfy import NotifyService


@dataclass
class AppMeta:
    app_id: str
    timestamp: datetime
    secrets: Dict[str, str]


class AppContext:
    def __init__(
        self,
        user: User,
        metrics: Any,
        db: AsyncSession,
        io: IOAdapter,
        event_bus: Any,
        notify: NotifyService,
        meta: AppMeta,
    ) -> None:
        self.user = user
        self.metrics = metrics
        self.db = db
        self.io = io
        self.notify = notify
        self.meta = meta
        self._event_bus = event_bus

    async def emit(self, topic: str, data: Any) -> None:
        await self._event_bus.emit(topic, data)

    async def cleanup(self) -> None:
        try:
            await self.db.rollback()
        except Exception as e:
            logger.warning("DB rollback failed during cleanup: %s", e)
        try:
            await self.db.close()
        except Exception as e:
            logger.warning("DB close failed during cleanup: %s", e)

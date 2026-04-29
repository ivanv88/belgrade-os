from __future__ import annotations
import logging
import json
from typing import Any, Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text

logger = logging.getLogger(__name__)

class AppContext:
    def __init__(
        self,
        app_id: str,
        user_id: Optional[str],
        tenant_id: Optional[str],
        trace_id: str,
        bridge_url: str,
        db_url: Optional[str] = None,
    ) -> None:
        self.app_id = app_id
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.trace_id = trace_id
        self._bridge_url = bridge_url
        self._db_url = db_url
        self._db_session: Optional[AsyncSession] = None

    @property
    async def db(self) -> AsyncSession:
        """Returns a DB session scoped to the current tenant and app."""
        if not self._db_url:
            raise RuntimeError("Database not configured for this app.")
        
        if not self._db_session:
            engine = create_async_engine(self._db_url)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            self._db_session = session_factory()
            
            # Physical isolation: set the search path to the tenant-app schema
            if self.tenant_id:
                schema_name = f"app_{self.tenant_id.replace('-', '_')}_{self.app_id.replace('-', '_')}"
                await self._db_session.execute(text(f"SET search_path TO {schema_name}, public"))
        
        return self._db_session

    async def notify(self, message: str, level: str = "info") -> None:
        """Sends a notification via the Bridge's notification service."""
        try:
            async with httpx.AsyncClient() as client:
                # We fetch the notification config from the bridge
                resp = await client.get(f"{self._bridge_url}/v1/notifications/provider")
                resp.raise_for_status()
                config = resp.json()
                
                # Push to ntfy.sh
                topic = config["topic"]
                if self.user_id:
                    # User-specific sub-topic if needed, or just standard topic
                    topic = f"{topic}-{self.user_id.split('@')[0]}"
                
                await client.post(
                    f"{config['base_url']}/{topic}",
                    content=message,
                    headers={"Title": f"Belgrade OS: {self.app_id}", "Priority": "3"}
                )
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    async def emit(self, topic: str, payload: Any) -> None:
        """Publishes an event to the internal Event Bus via the Bridge."""
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self._bridge_url}/v1/events/publish",
                    json={
                        "topic": topic,
                        "payload": payload,
                        "app_id": self.app_id,
                        "tenant_id": self.tenant_id,
                        "trace_id": self.trace_id
                    }
                )
        except Exception as e:
            logger.error(f"Failed to emit event {topic}: {e}")

    async def cleanup(self) -> None:
        """Closes the DB session if it was opened."""
        if self._db_session:
            await self._db_session.close()

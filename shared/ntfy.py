from __future__ import annotations
from typing import Optional
import httpx


NTFY_BASE_URL = "https://ntfy.sh"


class NotifyService:
    def __init__(self, topic: Optional[str]) -> None:
        self._topic = topic

    async def send(
        self,
        message: str,
        title: str = "",
        priority: str = "default",
    ) -> None:
        if not self._topic:
            return
        headers: dict[str, str] = {"Priority": priority}
        if title:
            headers["Title"] = title
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{NTFY_BASE_URL}/{self._topic}",
                content=message.encode(),
                headers=headers,
            )

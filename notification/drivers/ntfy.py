from __future__ import annotations
from typing import Optional
import httpx
from gen import belgrade_os_pb2
from .base import NotificationDriver

# Maps proto NotificationPriority values to ntfy priority integers.
# ntfy scale: 1=min, 2=low, 3=default, 4=high, 5=urgent
_PRIORITY_MAP: dict[int, str] = {
    belgrade_os_pb2.NOTIFICATION_PRIORITY_UNSPECIFIED: "3",
    belgrade_os_pb2.NOTIFICATION_LOW: "2",
    belgrade_os_pb2.NOTIFICATION_NORMAL: "3",
    belgrade_os_pb2.NOTIFICATION_HIGH: "5",
}


class NtfyDriver(NotificationDriver):
    def __init__(self, base_url: str, topic: str, client: Optional[httpx.AsyncClient] = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._topic = topic
        self._client = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def send(self, req: belgrade_os_pb2.NotificationRequest) -> None:
        priority = _PRIORITY_MAP.get(req.priority, "3")
        headers: dict[str, str] = {
            "Title": req.title,
            "Priority": priority,
        }
        if req.tags:
            headers["Tags"] = ",".join(req.tags)

        # Use own client if provided, otherwise context managed one
        if self._client:
            resp = await self._client.post(
                f"{self._base_url}/{self._topic}",
                content=req.body,
                headers=headers,
            )
            resp.raise_for_status()
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._base_url}/{self._topic}",
                    content=req.body,
                    headers=headers,
                )
                resp.raise_for_status()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

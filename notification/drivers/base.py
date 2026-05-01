from __future__ import annotations
from abc import ABC, abstractmethod
from gen import belgrade_os_pb2


class NotificationDriver(ABC):
    @abstractmethod
    async def send(self, req: belgrade_os_pb2.NotificationRequest) -> None:
        """Send the notification. Raise on unrecoverable delivery failure."""
        ...

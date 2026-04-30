from __future__ import annotations
import socket
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    redis_url: str = "redis://localhost:6379"
    ntfy_base_url: str = "https://ntfy.sh"
    ntfy_topic: str = "belgrade-os"
    notification_driver: str = "ntfy"
    worker_id: str = ""
    model_config = {"env_file": ".env"}

    @property
    def effective_worker_id(self) -> str:
        return self.worker_id or socket.gethostname()


def load_config() -> Config:
    return Config()

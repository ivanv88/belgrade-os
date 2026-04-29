from __future__ import annotations
import socket
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    redis_url: str = "redis://localhost:6379"
    bridge_url: str = "http://localhost:8081"
    worker_id: str = ""
    lease_ttl_s: int = 60
    tool_timeout_s: int = 30
    model_config = {"env_file": ".env"}

    @property
    def effective_worker_id(self) -> str:
        return self.worker_id or socket.gethostname()


def load_config() -> Config:
    return Config()

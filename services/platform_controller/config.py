from __future__ import annotations
import socket
from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    db_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
        validation_alias=AliasChoices("DATABASE_URL"),
    )
    redis_url: str = Field(
        default="redis://localhost:6379",
        validation_alias=AliasChoices("CONTROLLER_REDIS_URL", "BEG_OS_REDIS_URL"),
    )
    bridge_url: str = Field(
        default="http://localhost:8081",
        validation_alias=AliasChoices("BEG_OS_BRIDGE_URL", "BRIDGE_URL"),
    )
    port: int = Field(default=8000, validation_alias=AliasChoices("CONTROLLER_PORT"))
    worker_id: str = ""
    model_config = {"env_file": ".env", "populate_by_name": True}

    @property
    def effective_worker_id(self) -> str:
        return self.worker_id or socket.gethostname()


def load_config() -> Config:
    return Config()

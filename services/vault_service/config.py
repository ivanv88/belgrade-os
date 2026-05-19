from __future__ import annotations
import socket
from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    vault_path: str = Field(
        default="/tmp/belgrade-vault",
        validation_alias=AliasChoices("BEG_OS_VAULT_PATH"),
    )
    redis_url: str = Field(
        default="redis://localhost:6379",
        validation_alias=AliasChoices("VAULT_REDIS_URL", "BEG_OS_REDIS_URL"),
    )
    worker_id: str = ""
    model_config = {"env_file": ".env", "populate_by_name": True}

    @property
    def effective_worker_id(self) -> str:
        return self.worker_id or socket.gethostname()


def load_config() -> Config:
    return Config()

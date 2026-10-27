from __future__ import annotations
import socket
from typing import Literal
from pydantic import Field, AliasChoices, model_validator
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
    # Loopback by default: GET /apps is unauthenticated and nothing off-host needs the API.
    host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("CONTROLLER_HOST"))
    port: int = Field(default=8000, validation_alias=AliasChoices("CONTROLLER_PORT"))
    # How apps run: "subprocess" (dev) or "systemd" (sandboxed units, production).
    app_runner: Literal["subprocess", "systemd"] = Field(
        default="subprocess", validation_alias=AliasChoices("CONTROLLER_APP_RUNNER"))
    app_env_dir: str = Field(
        default="/run/belgrade-os/apps", validation_alias=AliasChoices("CONTROLLER_APP_ENV_DIR"))
    # Derives each app's DB role password (app_db.py). Empty = apps share the platform DB user (dev only).
    app_db_secret: str = Field(default="", validation_alias=AliasChoices("APP_DB_SECRET"))
    worker_id: str = ""
    # The repo-root .env is shared by every service; take only our own keys.
    model_config = {"env_file": ".env", "populate_by_name": True, "extra": "ignore"}

    @model_validator(mode="after")
    def _check_app_db_secret(self) -> "Config":
        if self.app_db_secret and len(self.app_db_secret) < 32:
            raise ValueError("APP_DB_SECRET must be at least 32 characters")
        if self.app_runner == "systemd" and not self.app_db_secret:
            raise ValueError("APP_DB_SECRET is required when CONTROLLER_APP_RUNNER=systemd")
        return self

    @property
    def effective_worker_id(self) -> str:
        return self.worker_id or socket.gethostname()


def load_config() -> Config:
    return Config()

import socket
from typing import Literal, Optional
from pydantic import Field, AliasChoices, model_validator
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    redis_url: str = Field(
        default="redis://localhost:6379",
        validation_alias=AliasChoices("INFERENCE_REDIS_URL", "REDIS_URL"),
    )
    provider: Literal["anthropic", "gemini", "ollama"]
    model: str
    max_tokens: int = 8192
    consumer_id: str = ""

    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    ollama_base_url: str = "http://localhost:11434"

    model_config = {"env_file": ".env", "populate_by_name": True}

    @model_validator(mode="after")
    def check_credentials(self) -> "Config":
        if self.provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY required when PROVIDER=anthropic")
        if self.provider == "gemini" and not self.google_api_key:
            raise ValueError("GOOGLE_API_KEY required when PROVIDER=gemini")
        return self

    @property
    def effective_consumer_id(self) -> str:
        return self.consumer_id or socket.gethostname()


def load_config() -> Config:
    return Config()

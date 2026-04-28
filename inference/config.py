import socket
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    redis_url: str = "redis://localhost:6379"
    anthropic_api_key: str
    model: str = "claude-opus-4-7"
    max_tokens: int = 8192
    consumer_id: str = ""

    model_config = {"env_file": ".env"}

    @property
    def effective_consumer_id(self) -> str:
        return self.consumer_id or socket.gethostname()


def load_config() -> Config:
    return Config()

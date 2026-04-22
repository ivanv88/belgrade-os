from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    db_password: str
    ntfy_topic: str = "laurent_beg_os_2026"
    gemini_api_key: str | None = None

    class Config:
        env_file = ".env"

settings = Settings()

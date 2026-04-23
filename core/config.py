from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    db_password: str
    ntfy_topic: str = ""
    tunnel_provider: str = "none"
    tunnel_auth_header: str = ""

    class Config:
        env_file = ".env"

settings = Settings()  # type: ignore[call-arg]  # pydantic-settings reads db_password from env

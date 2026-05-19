from __future__ import annotations
from pydantic import Field
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    bridge_url: str = Field(default="http://localhost:8081", validation_alias="BRIDGE_URL")
    port: int = Field(default=8083, validation_alias="MCP_PORT")
    default_user_id: str = Field(default="", validation_alias="MCP_DEFAULT_USER_ID")
    default_tenant_id: str = Field(default="", validation_alias="MCP_DEFAULT_TENANT_ID")
    mcp_jwt_secret: str = Field(default="", validation_alias="MCP_JWT_SECRET")
    cf_team_domain: str = Field(default="", validation_alias="CF_TEAM_DOMAIN")
    cf_mcp_audience: str = Field(default="", validation_alias="CF_MCP_AUDIENCE")
    model_config = {"env_file": ".env", "populate_by_name": True}


def load_config() -> Config:
    return Config()

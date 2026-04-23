from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator


class AppTrigger(BaseModel):
    type: Literal["cron", "observer", "hook", "event"]
    schedule: Optional[str] = None
    path: Optional[str] = None
    topic: Optional[str] = None

    @model_validator(mode="after")
    def validate_fields(self) -> "AppTrigger":
        if self.type == "cron" and not self.schedule:
            raise ValueError("schedule is required for cron triggers")
        if self.type == "observer" and not self.path:
            raise ValueError("path is required for observer triggers")
        if self.type == "event":
            if not self.topic:
                raise ValueError("topic is required for event triggers")
            if "." not in self.topic:
                raise ValueError("topic must be namespaced: {origin}.{event_type}")
        return self


class AppStorage(BaseModel):
    scope: str
    adapter: Literal["local", "obsidian", "gdrive"] = "local"


class AppConfig(BaseModel):
    shared_write: bool = False
    model_config = {"extra": "allow"}


class AppManifest(BaseModel):
    app_id: str = Field(..., pattern=r"^[a-z0-9-]+$")
    name: str
    description: str
    version: str
    pattern: Literal["worker", "observer", "bridge", "orchestrator"]
    mcp_enabled: bool = False
    triggers: List[AppTrigger]
    storage: AppStorage
    config: AppConfig = Field(default_factory=AppConfig)

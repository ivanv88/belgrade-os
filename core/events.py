from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel


class SystemAppFailed(BaseModel):
    app_id: str
    error: str
    timestamp: datetime


class SystemCircuitBroken(BaseModel):
    app_id: str
    failure_count: int


class SystemStorageLow(BaseModel):
    path: str
    available_bytes: int


SYSTEM_SCHEMAS: dict[str, type[BaseModel]] = {
    "system.app_failed": SystemAppFailed,
    "system.circuit_broken": SystemCircuitBroken,
    "system.storage_low": SystemStorageLow,
}

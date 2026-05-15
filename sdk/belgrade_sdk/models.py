from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema_json: str
    mcp_hint: Optional[str] = None

class RegisterRequest(BaseModel):
    app_id: str
    callback_url: str
    tools: List[ToolDefinition]
    subscriptions: Optional[List[str]] = None
    mcp: bool = False

class EventPayload(BaseModel):
    topic: str
    payload: Any
    app_id: Optional[str] = None
    tenant_id: Optional[str] = None
    trace_id: str

class ExecuteRequest(BaseModel):
    tool_name: str
    input_json: str
    trace_id: str
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None

class ExecuteResponse(BaseModel):
    success: bool
    output_json: str = ""
    error: str = ""

class UIBundleDefinition(BaseModel):
    type: str = "spa"
    path: str = "static/"
    entry: str = "index.html"
    required_role: Optional[str] = None

class AppUIConfig(BaseModel):
    enabled: bool = False
    bundles: Dict[str, UIBundleDefinition] = {}

class NotificationsConfig(BaseModel):
    driver: Optional[str] = None

class AppManifest(BaseModel):
    app_id: str
    name: Optional[str] = None
    ui: Optional[AppUIConfig] = None
    related_apps: List[str] = []
    notifications: Optional[NotificationsConfig] = None

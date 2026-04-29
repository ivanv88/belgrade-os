from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema_json: str

class RegisterRequest(BaseModel):
    app_id: str
    callback_url: str
    tools: List[ToolDefinition]
    subscriptions: Optional[List[str]] = None

class EventPayload(BaseModel):
    topic: str
    payload: Any
    app_id: str
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

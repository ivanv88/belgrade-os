from __future__ import annotations
import os
import json
import logging
import inspect
from typing import Any, Callable, Dict, List, Optional
import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException

from .models import RegisterRequest, ToolDefinition, ExecuteRequest, ExecuteResponse, EventPayload
from .context import AppContext

logger = logging.getLogger(__name__)

class BelgradeApp:
    def __init__(self, app_id: str):
        self.app_id = app_id
        self.tools: Dict[str, Callable] = {}
        self.tool_definitions: List[ToolDefinition] = []
        self.event_handlers: Dict[str, List[Callable]] = {}
        
        self.bridge_url = os.getenv("BEG_OS_BRIDGE_URL", "http://localhost:8081")
        self.db_url = os.getenv("BEG_OS_DB_URL")
        self.callback_url = os.getenv("BEG_OS_CALLBACK_URL", "http://localhost:9000")
        
        self.app = FastAPI(title=f"Belgrade App: {app_id}")
        self._setup_routes()

    def tool(self, name: str, description: str):
        """Decorator to register a tool."""
        def decorator(func: Callable):
            # Inspect function to generate JSON Schema (simplified for now)
            sig = inspect.signature(func)
            schema = {"type": "object", "properties": {}}
            for param_name, param in sig.parameters.items():
                if param_name == "ctx": continue
                schema["properties"][param_name] = {"type": "string"} # Default to string
            
            full_name = f"{self.app_id}:{name}"
            self.tools[full_name] = func
            self.tool_definitions.append(ToolDefinition(
                name=full_name,
                description=description,
                input_schema_json=json.dumps(schema)
            ))
            return func
        return decorator

    def on_event(self, topic: str):
        """Decorator to register an event handler."""
        def decorator(func: Callable):
            self.event_handlers.setdefault(topic, []).append(func)
            return func
        return decorator

    def _setup_routes(self):
        @self.app.post("/execute")
        async def execute(req: ExecuteRequest) -> ExecuteResponse:
            if req.tool_name not in self.tools:
                return ExecuteResponse(success=False, error=f"Tool {req.tool_name} not found")
            
            ctx = AppContext(
                app_id=self.app_id,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                trace_id=req.trace_id,
                bridge_url=self.bridge_url,
                db_url=self.db_url
            )
            
            try:
                func = self.tools[req.tool_name]
                kwargs = json.loads(req.input_json)
                result = await func(ctx, **kwargs)
                return ExecuteResponse(success=True, output_json=json.dumps(result))
            except Exception as e:
                logger.exception("Tool execution failed")
                return ExecuteResponse(success=False, error=str(e))
            finally:
                await ctx.cleanup()

        @self.app.post("/events")
        async def handle_event(event: EventPayload):
            if event.topic in self.event_handlers:
                ctx = AppContext(
                    app_id=self.app_id,
                    user_id=None,
                    tenant_id=event.tenant_id,
                    trace_id=event.trace_id,
                    bridge_url=self.bridge_url,
                    db_url=self.db_url,
                    redis_url=self.redis_url,
                    notification_driver=self.notification_driver,
                )
                try:
                    for handler in self.event_handlers[event.topic]:
                        # Handle both sync and async handlers
                        if inspect.iscoroutinefunction(handler):
                            await handler(ctx, event.payload)
                        else:
                            handler(ctx, event.payload)
                except Exception:
                    logger.exception(f"Event handler failed for topic {event.topic}")
                finally:
                    await ctx.cleanup()
            return {"status": "ok"}

        @self.app.get("/health")
        async def health():
            return {
                "status": "ok", 
                "app_id": self.app_id, 
                "tools": list(self.tools.keys()),
                "subscriptions": list(self.event_handlers.keys())
            }

    async def register_with_bridge(self):
        """Notifies the Capability Bridge about available tools and subscriptions."""
        req = RegisterRequest(
            app_id=self.app_id,
            callback_url=self.callback_url,
            tools=self.tool_definitions,
            subscriptions=list(self.event_handlers.keys())
        )
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(f"{self.bridge_url}/v1/register", json=req.model_dump())
                resp.raise_for_status()
                logger.info(f"Registered {len(self.tools)} tools and {len(self.event_handlers)} subscriptions with bridge")
            except Exception as e:
                logger.error(f"Failed to register with bridge: {e}")

    def run(self, host: str = "0.0.0.0", port: int = 9000):
        """Starts the app server and registers tools."""
        @self.app.on_event("startup")
        async def on_startup():
            await self.register_with_bridge()
            
        uvicorn.run(self.app, host=host, port=port)
       uvicorn.run(self.app, host=host, port=port)

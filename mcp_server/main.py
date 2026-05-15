from __future__ import annotations
import json
import os
from typing import Optional
import jwt as _jwt
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import oauth
import registry

app = FastAPI(title="Belgrade OS MCP Server")

_security = HTTPBearer(auto_error=False)

_MCP_DEFAULT_USER_ID = os.getenv("MCP_DEFAULT_USER_ID", "")
_MCP_DEFAULT_TENANT_ID = os.getenv("MCP_DEFAULT_TENANT_ID", "")


async def _require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=401, detail="missing token")
    try:
        return oauth.validate_token(credentials.credentials)
    except (_jwt.PyJWTError, ValueError):
        raise HTTPException(status_code=401, detail="invalid token")


@app.post("/oauth/token")
async def token_endpoint(request: Request):
    form = await request.form()
    grant_type = form.get("grant_type")
    if grant_type != "client_credentials":
        raise HTTPException(status_code=400, detail="unsupported_grant_type")

    cf_jwt = request.headers.get("CF-Access-Jwt-Assertion")
    if not cf_jwt:
        raise HTTPException(status_code=401, detail="missing CF JWT")

    try:
        oauth.validate_cf_jwt(cf_jwt)
    except ValueError:
        raise  # server misconfiguration — propagate as 500
    except Exception:
        raise HTTPException(status_code=401, detail="invalid CF service token")

    token = oauth.issue_token(_MCP_DEFAULT_USER_ID, _MCP_DEFAULT_TENANT_ID)
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600}


@app.post("/mcp")
async def mcp_handler(
    request: Request,
    claims: dict = Depends(_require_auth),
):
    body = await request.json()
    method = body.get("method", "")
    params = body.get("params") or {}
    req_id = body.get("id")

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "result": result, "id": req_id}

    def err(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id}

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "belgrade-os", "version": "1.0.0"},
        })

    if method == "tools/list":
        try:
            tools = await registry.list_mcp_tools()
            mcp_tools = []
            for t in tools:
                description = t["description"].rstrip(".")
                if t.get("mcp_hint"):
                    description = f"{description}. {t['mcp_hint'].rstrip('.')}."
                else:
                    description += "."
                mcp_tools.append({
                    "name": t["name"],
                    "description": description,
                    "inputSchema": json.loads(t["input_schema_json"]),
                })
            return ok({"tools": mcp_tools})
        except Exception as exc:
            return err(-32603, f"bridge unavailable: {exc}")

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            mcp_tools = await registry.list_mcp_tools()
        except Exception as exc:
            return err(-32603, f"bridge unavailable: {exc}")
        exposed_names = {t["name"] for t in mcp_tools}
        if tool_name not in exposed_names:
            return err(-32602, f"tool not found: {tool_name}")
        try:
            result = await registry.call_tool(
                tool_name,
                arguments,
                claims.get("user_id", _MCP_DEFAULT_USER_ID),
                claims.get("tenant_id", _MCP_DEFAULT_TENANT_ID),
            )
        except Exception as exc:
            return err(-32603, f"tool execution failed: {exc}")
        if not result.get("success"):
            return err(-32603, result.get("error", "tool execution failed"))
        output = result.get("output_json", "{}")
        return ok({"content": [{"type": "text", "text": output}]})

    return err(-32601, f"method not found: {method}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8083")))

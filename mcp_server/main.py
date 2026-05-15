from __future__ import annotations
import os
from typing import Optional
import jwt as _jwt
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import oauth

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
    except _jwt.PyJWTError:
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
    except Exception:
        raise HTTPException(status_code=401, detail="invalid CF service token")

    token = oauth.issue_token(_MCP_DEFAULT_USER_ID, _MCP_DEFAULT_TENANT_ID)
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600}


@app.post("/mcp")
async def mcp_handler(
    request: Request,
    claims: dict = Depends(_require_auth),
):
    # Placeholder — MCP protocol implemented in Task 5
    return {"jsonrpc": "2.0", "result": {}, "id": None}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8083")))

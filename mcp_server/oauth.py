from __future__ import annotations
import os
from typing import Optional
import jwt
from datetime import datetime, timedelta, timezone
from jwt import PyJWKClient

_CF_TEAM_DOMAIN = os.getenv("CF_TEAM_DOMAIN", "")
_CF_MCP_AUDIENCE = os.getenv("CF_MCP_AUDIENCE", "")
_MCP_JWT_SECRET = os.getenv("MCP_JWT_SECRET", "")

_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url = f"https://{_CF_TEAM_DOMAIN}.cloudflareaccess.com/cdn-cgi/access/certs"
        _jwks_client = PyJWKClient(url)
    return _jwks_client


def validate_cf_jwt(token: str) -> dict:
    """Validate a Cloudflare Access JWT. Raises on invalid token."""
    client = _get_jwks_client()
    signing_key = client.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=_CF_MCP_AUDIENCE,
    )


def issue_token(user_id: str, tenant_id: str) -> str:
    """Issue a short-lived Belgrade JWT for MCP sessions."""
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, _MCP_JWT_SECRET, algorithm="HS256")


def validate_token(token: str) -> dict:
    """Validate a Belgrade JWT. Returns claims dict. Raises on invalid."""
    return jwt.decode(token, _MCP_JWT_SECRET, algorithms=["HS256"])

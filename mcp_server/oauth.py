from __future__ import annotations
import os
from typing import Optional
import jwt
from datetime import datetime, timedelta, timezone
from jwt import PyJWKClient

_jwks_client: Optional[PyJWKClient] = None


def _get_jwt_secret() -> str:
    secret = os.getenv("MCP_JWT_SECRET", "")
    if not secret:
        raise ValueError("MCP_JWT_SECRET is required")
    return secret


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        domain = os.getenv("CF_TEAM_DOMAIN", "")
        if not domain:
            raise ValueError("CF_TEAM_DOMAIN is required")
        url = f"https://{domain}.cloudflareaccess.com/cdn-cgi/access/certs"
        _jwks_client = PyJWKClient(url)
    return _jwks_client


def validate_cf_jwt(token: str) -> dict:
    """Validate a Cloudflare Access JWT. Raises on invalid token."""
    audience = os.getenv("CF_MCP_AUDIENCE", "")
    client = _get_jwks_client()
    signing_key = client.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=audience,
    )


def issue_token(user_id: str, tenant_id: str) -> str:
    """Issue a short-lived Belgrade JWT for MCP sessions."""
    payload = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=1),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm="HS256")


def validate_token(token: str) -> dict:
    """Validate a Belgrade JWT. Returns claims dict. Raises on invalid."""
    return jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])

from __future__ import annotations
import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def verify_request_identity(headers: dict[str, str]) -> None:
    """
    Infrastructure-agnostic identity check.
    Fails if the expected tunnel header is missing, unless auth is
    explicitly offloaded to the network layer (tunnel_provider = none).
    """
    from core.config import settings
    if settings.tunnel_provider == "none":
        # Auth handled at the network layer (Tailscale, VPN, etc.)
        return

    auth_identity = headers.get(settings.tunnel_auth_header.lower())

    if not auth_identity:
        logger.error("Missing security header: %s", settings.tunnel_auth_header)
        raise HTTPException(
            status_code=401,
            detail=f"Authentication required via {settings.tunnel_provider}",
        )

    logger.info("Identity verified: %s", auth_identity)

from __future__ import annotations
import pytest
from unittest.mock import patch
from fastapi import HTTPException
from core.auth import verify_request_identity


def cloudflare_headers(token: str = "jwt-token") -> dict[str, str]:
    return {"x-cloudflare-access-identity": token}


def test_cloudflare_provider_valid_header() -> None:
    with patch("core.config.settings") as mock_settings:
        mock_settings.tunnel_provider = "cloudflare"
        mock_settings.tunnel_auth_header = "X-Cloudflare-Access-Identity"
        verify_request_identity(cloudflare_headers("valid-jwt"))


def test_cloudflare_provider_missing_header_raises() -> None:
    with patch("core.config.settings") as mock_settings:
        mock_settings.tunnel_provider = "cloudflare"
        mock_settings.tunnel_auth_header = "X-Cloudflare-Access-Identity"
        with pytest.raises(HTTPException) as exc:
            verify_request_identity({})
        assert exc.value.status_code == 401
        assert "cloudflare" in exc.value.detail.lower()


def test_none_provider_skips_check() -> None:
    with patch("core.config.settings") as mock_settings:
        mock_settings.tunnel_provider = "none"
        # No headers at all — should pass because network layer handles auth
        verify_request_identity({})


def test_custom_provider_and_header() -> None:
    with patch("core.config.settings") as mock_settings:
        mock_settings.tunnel_provider = "tailscale"
        mock_settings.tunnel_auth_header = "Tailscale-User-Login"
        verify_request_identity({"tailscale-user-login": "ivan@example.com"})


def test_custom_provider_missing_header_raises() -> None:
    with patch("core.config.settings") as mock_settings:
        mock_settings.tunnel_provider = "tailscale"
        mock_settings.tunnel_auth_header = "Tailscale-User-Login"
        with pytest.raises(HTTPException) as exc:
            verify_request_identity({})
        assert exc.value.status_code == 401
        assert "tailscale" in exc.value.detail.lower()

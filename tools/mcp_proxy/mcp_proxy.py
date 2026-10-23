#!/usr/bin/env python3
"""Local stdio MCP server that forwards to Belgrade OS's remote MCP endpoint.

Claude Desktop can't send Cloudflare Access service-token headers or run the
client_credentials exchange, so it runs this proxy as a local MCP server:

    Claude Desktop --stdio--> mcp_proxy --HTTPS--> Cloudflare Access --> mcp_server

Credentials: BELGRADE_CF_CLIENT_ID / BELGRADE_CF_CLIENT_SECRET, else macOS
Keychain (account "belgrade-mcp", services "cf-access-client-id" and
"cf-access-client-secret"). Standard library only, so any python3 can run it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional, Tuple

DEFAULT_URL = "https://mcp.beg-os.fyi"
KEYCHAIN_ACCOUNT = "belgrade-mcp"
# Refresh this long before expiry so a request never races the deadline.
REFRESH_MARGIN_S = 60
TIMEOUT_S = 30
# Cloudflare's Browser Integrity Check blocks the default `Python-urllib/x.y`
# User-Agent (error 1010) before Access evaluates the service token.
USER_AGENT = "belgrade-mcp-proxy/1.0"


class UpstreamError(Exception):
    pass


class Proxy:
    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._access_headers = {
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        }
        self._clock = clock
        self._token: Optional[str] = None
        self._token_expires_at = 0.0

    def handle(self, line: str) -> Optional[str]:
        """Forward one JSON-RPC message; returns the reply line, or None for notifications."""
        msg = json.loads(line)
        is_notification = "id" not in msg
        try:
            reply = self._forward(msg)
        except UpstreamError as exc:
            log(f"error: {exc}")
            if is_notification:
                return None
            reply = {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(exc)}, "id": msg["id"]}
        return None if is_notification else json.dumps(reply)

    def _forward(self, msg: dict) -> dict:
        body = json.dumps(msg).encode()
        status, reply = self._post_mcp(body, self._get_token())
        if status == 401:
            # Token revoked or server secret rotated — get a fresh one and retry once.
            self._token = None
            status, reply = self._post_mcp(body, self._get_token())
        if status != 200:
            raise UpstreamError(f"MCP endpoint returned HTTP {status}: {reply}")
        return reply

    def _post_mcp(self, body: bytes, token: str) -> Tuple[int, dict]:
        headers = {**self._access_headers, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        return self._post("/mcp", body, headers)

    def _get_token(self) -> str:
        if self._token and self._clock() < self._token_expires_at - REFRESH_MARGIN_S:
            return self._token
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        headers = {**self._access_headers, "Content-Type": "application/x-www-form-urlencoded"}
        status, reply = self._post("/oauth/token", body, headers)
        if status != 200:
            raise UpstreamError(f"token exchange returned HTTP {status}: {reply}")
        self._token = reply["access_token"]
        self._token_expires_at = self._clock() + float(reply.get("expires_in", 3600))
        return self._token

    def _post(self, path: str, body: bytes, headers: dict) -> Tuple[int, dict]:
        headers = {**headers, "User-Agent": USER_AGENT}
        req = urllib.request.Request(self.base_url + path, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return resp.status, _json_or_text(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _json_or_text(exc.read())
        except (urllib.error.URLError, OSError) as exc:
            raise UpstreamError(f"cannot reach {self.base_url}: {exc}") from exc


def _json_or_text(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        # Cloudflare error pages are HTML; keep the message short.
        return raw.decode(errors="replace")[:200]


def _keychain(service: str) -> str:
    out = subprocess.run(
        ["security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", service, "-w"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def load_credentials() -> Tuple[str, str]:
    client_id = os.environ.get("BELGRADE_CF_CLIENT_ID")
    client_secret = os.environ.get("BELGRADE_CF_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret
    return _keychain("cf-access-client-id"), _keychain("cf-access-client-secret")


def log(message: str) -> None:
    # stdout carries the protocol; Claude Desktop collects stderr in its MCP logs.
    print(f"[belgrade-mcp-proxy] {message}", file=sys.stderr, flush=True)


def main() -> None:
    url = os.environ.get("BELGRADE_MCP_URL", DEFAULT_URL)
    try:
        client_id, client_secret = load_credentials()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        log(f"cannot load Cloudflare service token from env or Keychain: {exc}")
        sys.exit(1)
    proxy = Proxy(url, client_id, client_secret)
    log(f"forwarding to {url}")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = proxy.handle(line)
        except ValueError as exc:
            log(f"ignoring malformed message: {exc}")
            continue
        if reply is not None:
            sys.stdout.write(reply + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

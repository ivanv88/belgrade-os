from __future__ import annotations
import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import pytest

import mcp_proxy
from mcp_proxy import Proxy


class _LocalServer(HTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind does a reverse DNS lookup (getfqdn) that can
        # stall for seconds on slow resolvers; a loopback test server needs none.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class FakeBelgrade:
    """Emulates Cloudflare Access + mcp_server: /oauth/token and /mcp."""

    def __init__(self):
        self.token_calls = 0
        self.mcp_calls: list[dict] = []
        self.expires_in = 3600
        self.reject_next_mcp_with_401 = False
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = self.rfile.read(int(self.headers["content-length"]))
                # Cloudflare Access: every request needs the service token headers.
                if (self.headers.get("CF-Access-Client-Id"), self.headers.get("CF-Access-Client-Secret")) != ("cid", "csecret"):
                    return self._reply(403, {"detail": "forbidden by Access"})
                if self.path == "/oauth/token":
                    assert parse_qs(body.decode())["grant_type"] == ["client_credentials"]
                    fake.token_calls += 1
                    return self._reply(200, {
                        "access_token": f"tok{fake.token_calls}", "token_type": "bearer",
                        "expires_in": fake.expires_in,
                    })
                if self.path == "/mcp":
                    msg = json.loads(body)
                    fake.mcp_calls.append({"auth": self.headers.get("Authorization"), "msg": msg})
                    if fake.reject_next_mcp_with_401:
                        fake.reject_next_mcp_with_401 = False
                        return self._reply(401, {"detail": "invalid token"})
                    return self._reply(200, {"jsonrpc": "2.0", "result": {"echo": msg["method"]}, "id": msg.get("id")})
                self._reply(404, {})

        self.server = _LocalServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake():
    f = FakeBelgrade()
    yield f
    f.close()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _proxy(fake, clock=None) -> Proxy:
    return Proxy(fake.url, "cid", "csecret", clock=clock or Clock())


def _request(method: str, id_: int = 1) -> str:
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": {}, "id": id_})


def test_forwards_request_with_access_headers_and_bearer_token(fake):
    out = _proxy(fake).handle(_request("initialize"))

    assert json.loads(out) == {"jsonrpc": "2.0", "result": {"echo": "initialize"}, "id": 1}
    assert fake.mcp_calls[0]["auth"] == "Bearer tok1"


def test_reuses_token_until_it_is_about_to_expire(fake):
    clock = Clock()
    proxy = _proxy(fake, clock)

    proxy.handle(_request("tools/list", 1))
    clock.now += 3000
    proxy.handle(_request("tools/list", 2))
    assert fake.token_calls == 1

    clock.now += 560  # within the refresh margin of the 3600s expiry
    proxy.handle(_request("tools/list", 3))
    assert fake.token_calls == 2
    assert fake.mcp_calls[-1]["auth"] == "Bearer tok2"


def test_refreshes_token_and_retries_once_on_401(fake):
    proxy = _proxy(fake)
    proxy.handle(_request("tools/list", 1))
    fake.reject_next_mcp_with_401 = True  # e.g. MCP_JWT_SECRET rotated on the server

    out = proxy.handle(_request("tools/list", 2))

    assert json.loads(out)["result"] == {"echo": "tools/list"}
    assert fake.token_calls == 2
    assert fake.mcp_calls[-1]["auth"] == "Bearer tok2"


def test_notifications_get_no_reply(fake):
    note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})

    assert _proxy(fake).handle(note) is None


def test_unreachable_server_becomes_jsonrpc_error_with_request_id():
    proxy = Proxy("http://127.0.0.1:9", "cid", "csecret", clock=Clock())

    reply = json.loads(proxy.handle(_request("tools/list", 7)))

    assert reply["id"] == 7
    assert reply["error"]["code"] == -32603


def test_access_rejection_is_reported_not_swallowed(fake):
    proxy = Proxy(fake.url, "cid", "wrong-secret", clock=Clock())

    reply = json.loads(proxy.handle(_request("initialize", 3)))

    assert reply["id"] == 3
    assert "403" in reply["error"]["message"]


def test_credentials_come_from_env_before_keychain(monkeypatch):
    monkeypatch.setenv("BELGRADE_CF_CLIENT_ID", "env-id")
    monkeypatch.setenv("BELGRADE_CF_CLIENT_SECRET", "env-secret")
    monkeypatch.setattr(mcp_proxy, "_keychain", lambda service: pytest.fail("keychain must not be read"))

    assert mcp_proxy.load_credentials() == ("env-id", "env-secret")


def test_credentials_fall_back_to_keychain(monkeypatch):
    monkeypatch.delenv("BELGRADE_CF_CLIENT_ID", raising=False)
    monkeypatch.delenv("BELGRADE_CF_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(mcp_proxy, "_keychain", lambda service: f"kc-{service}")

    assert mcp_proxy.load_credentials() == ("kc-cf-access-client-id", "kc-cf-access-client-secret")

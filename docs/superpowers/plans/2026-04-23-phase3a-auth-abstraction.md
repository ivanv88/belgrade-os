# Phase 3a — Auth Abstraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ad-hoc `verify_request_identity()` function with a proper strategy pattern — an `AuthProvider` protocol with swappable implementations — so auth providers can be added or changed without touching call sites.

**Architecture:** A frozen `Identity` dataclass carries the resolved identity (email + provider). An `AuthProvider` protocol defines a single `resolve(headers) -> Identity` method. Two concrete providers ship: `NoneAuthProvider` (network-layer auth, returns a local sentinel identity) and `CloudflareAuthProvider` (validates the `X-Cloudflare-Access-Identity` header). A top-level `resolve_identity()` function reads `settings.auth_provider`, picks the right provider, and returns the `Identity`. All call sites swap `verify_request_identity()` for `resolve_identity()`.

**Tech Stack:** Python 3.12+, FastAPI, pydantic-settings, pytest, mypy (strict).

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `core/models/identity.py` | `Identity` frozen dataclass |
| Modify | `core/config.py` | Replace `tunnel_provider` + `tunnel_auth_header` with `auth_provider` |
| Rewrite | `core/auth.py` | `AuthProvider` protocol, `NoneAuthProvider`, `CloudflareAuthProvider`, `get_auth_provider()`, `resolve_identity()` |
| Modify | `core/loader.py` | `_register_hook` — swap `verify_request_identity` → `resolve_identity` |
| Modify | `core/mcp.py` | `run_app` — swap `verify_request_identity` → `resolve_identity` |
| Modify | `.env.example` | Rename `TUNNEL_PROVIDER`/`TUNNEL_AUTH_HEADER` → `AUTH_PROVIDER` |
| Create | `tests/core/models/test_identity.py` | Unit tests for `Identity` dataclass |
| Rewrite | `tests/core/test_auth.py` | Replace old `verify_request_identity` tests with strategy-pattern tests |

---

### Task 1: `Identity` dataclass

**Files:**
- Create: `core/models/identity.py`
- Create: `tests/core/models/test_identity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/models/test_identity.py
from __future__ import annotations
import pytest
from core.models.identity import Identity


def test_identity_stores_fields() -> None:
    identity = Identity(email="ivan@example.com", provider="cloudflare")
    assert identity.email == "ivan@example.com"
    assert identity.provider == "cloudflare"


def test_identity_is_frozen() -> None:
    identity = Identity(email="ivan@example.com", provider="cloudflare")
    with pytest.raises(Exception):
        identity.email = "other@example.com"  # type: ignore[misc]


def test_identity_equality() -> None:
    a = Identity(email="ivan@example.com", provider="cloudflare")
    b = Identity(email="ivan@example.com", provider="cloudflare")
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/core/models/test_identity.py -v
```

Expected: `ModuleNotFoundError: No module named 'core.models.identity'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/models/identity.py
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    email: str
    provider: str
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/core/models/test_identity.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/models/identity.py tests/core/models/test_identity.py
git commit -m "feat: add Identity frozen dataclass"
```

---

### Task 2: Update `core/config.py` — rename env var

**Files:**
- Modify: `core/config.py`

The existing `tunnel_provider` and `tunnel_auth_header` settings are Cloudflare-framed names. `auth_provider` is provider-agnostic and maps directly to the strategy we're adding. `tunnel_auth_header` is removed because each concrete `AuthProvider` owns its own header constant — callers don't configure headers, they configure which provider to use.

- [ ] **Step 1: Write the failing test**

Add this test to `tests/core/test_auth.py` (temporarily, to drive the config change):

```python
# append to tests/core/test_auth.py temporarily — will be replaced in Task 3
from unittest.mock import patch

def test_config_has_auth_provider() -> None:
    with patch("core.config.settings") as mock:
        mock.auth_provider = "none"
        assert mock.auth_provider == "none"
```

Actually, since `config.py` is a pydantic model, we verify by importing and checking the field exists:

```python
# tests/core/test_auth.py — add this function
def test_settings_has_auth_provider_field() -> None:
    from pydantic_settings import BaseSettings
    from core.config import Settings
    fields = Settings.model_fields
    assert "auth_provider" in fields
    assert "tunnel_provider" not in fields
    assert "tunnel_auth_header" not in fields
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/core/test_auth.py::test_settings_has_auth_provider_field -v
```

Expected: FAIL — `tunnel_provider` still present, `auth_provider` not found

- [ ] **Step 3: Write minimal implementation**

```python
# core/config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_password: str
    ntfy_topic: str = ""
    auth_provider: str = "none"

    class Config:
        env_file = ".env"


settings = Settings()  # type: ignore[call-arg]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/core/test_auth.py::test_settings_has_auth_provider_field -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/config.py
git commit -m "feat: rename tunnel_provider → auth_provider in config"
```

---

### Task 3: Rewrite `core/auth.py` with strategy pattern

**Files:**
- Rewrite: `core/auth.py`
- Rewrite: `tests/core/test_auth.py`

This is the core of the plan. The strategy pattern here means: `resolve_identity()` (the call site) doesn't know which provider is active — it delegates to whichever `AuthProvider` `get_auth_provider()` returns based on the config value. Adding a new provider (Tailscale, Keycloak, etc.) means adding a new class and one branch in `get_auth_provider()`. No call sites change.

`NoneAuthProvider` returns `Identity(email="local", provider="none")` as a sentinel — not "no auth" but "this machine is the user." It makes the return type uniform so callers always get an `Identity`.

`CloudflareAuthProvider` uses the `X-Cloudflare-Access-Identity` header. Cloudflare Zero Trust injects this after the user passes email OTP — it's a JWT containing the verified email. For now we use the raw header value as the email; full JWT verification is Phase 3b scope.

`get_auth_provider()` is a pure function (takes a string, returns a provider) — no global state, easy to test.

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_auth.py  (full replacement)
from __future__ import annotations
import pytest
from unittest.mock import patch
from fastapi import HTTPException
from core.auth import (
    NoneAuthProvider,
    CloudflareAuthProvider,
    get_auth_provider,
    resolve_identity,
)
from core.models.identity import Identity
from core.config import Settings


def test_none_provider_returns_local_identity() -> None:
    provider = NoneAuthProvider()
    identity = provider.resolve({})
    assert identity == Identity(email="local", provider="none")


def test_none_provider_ignores_headers() -> None:
    provider = NoneAuthProvider()
    identity = provider.resolve({"x-cloudflare-access-identity": "someone@example.com"})
    assert identity.email == "local"


def test_cloudflare_provider_valid_header() -> None:
    provider = CloudflareAuthProvider()
    identity = provider.resolve({"x-cloudflare-access-identity": "ivan@example.com"})
    assert identity.email == "ivan@example.com"
    assert identity.provider == "cloudflare"


def test_cloudflare_provider_missing_header_raises() -> None:
    provider = CloudflareAuthProvider()
    with pytest.raises(HTTPException) as exc:
        provider.resolve({})
    assert exc.value.status_code == 401
    assert "cloudflare" in exc.value.detail.lower()


def test_get_auth_provider_none() -> None:
    assert isinstance(get_auth_provider("none"), NoneAuthProvider)


def test_get_auth_provider_cloudflare() -> None:
    assert isinstance(get_auth_provider("cloudflare"), CloudflareAuthProvider)


def test_get_auth_provider_unknown_falls_back_to_none() -> None:
    # Unknown providers default to NoneAuthProvider so a typo doesn't lock
    # out the user — they'll notice auth isn't working and fix the config.
    assert isinstance(get_auth_provider("tailscale"), NoneAuthProvider)


def test_resolve_identity_none_provider() -> None:
    with patch("core.config.settings") as mock_settings:
        mock_settings.auth_provider = "none"
        identity = resolve_identity({})
    assert identity == Identity(email="local", provider="none")


def test_resolve_identity_cloudflare_valid() -> None:
    with patch("core.config.settings") as mock_settings:
        mock_settings.auth_provider = "cloudflare"
        identity = resolve_identity({"x-cloudflare-access-identity": "ivan@example.com"})
    assert identity.email == "ivan@example.com"


def test_resolve_identity_cloudflare_missing_header_raises() -> None:
    with patch("core.config.settings") as mock_settings:
        mock_settings.auth_provider = "cloudflare"
        with pytest.raises(HTTPException) as exc:
            resolve_identity({})
        assert exc.value.status_code == 401


def test_settings_has_auth_provider_field() -> None:
    fields = Settings.model_fields
    assert "auth_provider" in fields
    assert "tunnel_provider" not in fields
    assert "tunnel_auth_header" not in fields
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/core/test_auth.py -v
```

Expected: ImportError — `NoneAuthProvider`, `CloudflareAuthProvider`, etc. not found in `core.auth`

- [ ] **Step 3: Write minimal implementation**

```python
# core/auth.py
from __future__ import annotations
import logging
from typing import Protocol, runtime_checkable
from fastapi import HTTPException
from core.models.identity import Identity

logger = logging.getLogger(__name__)


@runtime_checkable
class AuthProvider(Protocol):
    def resolve(self, headers: dict[str, str]) -> Identity: ...


class NoneAuthProvider:
    def resolve(self, headers: dict[str, str]) -> Identity:
        return Identity(email="local", provider="none")


class CloudflareAuthProvider:
    _HEADER = "x-cloudflare-access-identity"

    def resolve(self, headers: dict[str, str]) -> Identity:
        value = headers.get(self._HEADER)
        if not value:
            logger.error("Missing Cloudflare identity header")
            raise HTTPException(
                status_code=401,
                detail="Authentication required via cloudflare",
            )
        logger.info("Identity verified via cloudflare: %s", value)
        return Identity(email=value, provider="cloudflare")


def get_auth_provider(auth_provider: str) -> AuthProvider:
    if auth_provider == "cloudflare":
        return CloudflareAuthProvider()
    return NoneAuthProvider()


def resolve_identity(headers: dict[str, str]) -> Identity:
    from core.config import settings
    return get_auth_provider(settings.auth_provider).resolve(headers)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/core/test_auth.py -v
```

Expected: 12 tests PASS (11 new + the field test from Task 2)

- [ ] **Step 5: Run mypy**

```bash
mypy core/auth.py core/models/identity.py
```

Expected: Success, no errors

- [ ] **Step 6: Commit**

```bash
git add core/auth.py tests/core/test_auth.py
git commit -m "feat: replace verify_request_identity with AuthProvider strategy pattern"
```

---

### Task 4: Update call sites — `_register_hook` and `run_app`

**Files:**
- Modify: `core/loader.py` (lines 185–188)
- Modify: `core/mcp.py` (lines 44–46)

Both callers currently call `verify_request_identity()` and discard the result. After this task they call `resolve_identity()` and discard the `Identity` return value. The `Identity` is available for future use (e.g., `ctx.identity`) without any further changes to call sites — that's Phase 3b.

- [ ] **Step 1: Verify existing loader + mcp tests still reference the old name (so we can confirm they pass after the rename)**

```bash
grep -n "verify_request_identity" tests/core/test_loader.py tests/core/test_mcp.py
```

Expected: no output (those tests mock at a higher level and don't reference `verify_request_identity` directly — the swap is transparent to them)

- [ ] **Step 2: Update `core/loader.py`**

Find this block (around line 185–188):

```python
def _register_hook(
    app: FastAPI,
    app_id: str,
    ctx_factory: Callable[[], Any],
    event_bus: EventBus,
) -> None:
    from core.auth import verify_request_identity

    async def handler(request: Request) -> Dict[str, str]:
        verify_request_identity(dict(request.headers))
```

Replace with:

```python
def _register_hook(
    app: FastAPI,
    app_id: str,
    ctx_factory: Callable[[], Any],
    event_bus: EventBus,
) -> None:
    from core.auth import resolve_identity

    async def handler(request: Request) -> Dict[str, str]:
        resolve_identity(dict(request.headers))
```

- [ ] **Step 3: Update `core/mcp.py`**

Find this block (around line 44–46):

```python
        async def run_app(
            request: Request,
            _app_id: str = app_id,
            _factory: Any = ctx_factory,
            _bus: Any = event_bus,
        ) -> dict[str, str]:
            from core.auth import verify_request_identity
            from core.executor import safe_execute
            verify_request_identity(dict(request.headers))
```

Replace with:

```python
        async def run_app(
            request: Request,
            _app_id: str = app_id,
            _factory: Any = ctx_factory,
            _bus: Any = event_bus,
        ) -> dict[str, str]:
            from core.auth import resolve_identity
            from core.executor import safe_execute
            resolve_identity(dict(request.headers))
```

- [ ] **Step 4: Confirm no remaining references to `verify_request_identity`**

```bash
grep -r "verify_request_identity" .
```

Expected: no output

- [ ] **Step 5: Run the full test suite**

```bash
pytest -v
```

Expected: all tests PASS (previously 56; now 56 + 3 identity + 12 auth = 71 tests if starting from scratch, or adjusted for replaced tests)

- [ ] **Step 6: Run mypy**

```bash
mypy .
```

Expected: Success, no errors

- [ ] **Step 7: Commit**

```bash
git add core/loader.py core/mcp.py
git commit -m "feat: update call sites to use resolve_identity()"
```

---

### Task 5: Update `.env.example`

**Files:**
- Modify: `.env.example`

The tunnel-framed env var names are gone. `AUTH_PROVIDER` is the single knob. Cloudflare's token is still needed for the tunnel daemon (`cloudflared`), but the header name is no longer configurable — `CloudflareAuthProvider` owns it internally.

- [ ] **Step 1: Replace the tunnel/auth section in `.env.example`**

Find this block:

```
# ── Tunnel & Auth ─────────────────────────────────────────────────────────────
# TUNNEL_PROVIDER controls which tunnel/auth mechanism protects the platform.
#
# Options:
#   none        — No header check. Auth is handled at the network layer
#                 (e.g. Tailscale, WireGuard, local-only). Safe for home LANs.
#
#   cloudflare  — Validates X-Cloudflare-Access-Identity header injected by
#                 Cloudflare Zero Trust. Requires cloudflared running on the host
#                 and a Zero Trust application configured at dash.cloudflare.com.
#                 TUNNEL_AUTH_HEADER must be set to: X-Cloudflare-Access-Identity
#
#   tailscale   — Validates a header injected by a Tailscale serve/funnel proxy.
#                 Set TUNNEL_AUTH_HEADER to whichever header your proxy injects
#                 (e.g. Tailscale-User-Login).
#
# Default: none
TUNNEL_PROVIDER=none
TUNNEL_AUTH_HEADER=

# ── Cloudflare (if TUNNEL_PROVIDER=cloudflare) ────────────────────────────────
# TUNNEL_PROVIDER=cloudflare
# TUNNEL_AUTH_HEADER=X-Cloudflare-Access-Identity
# CLOUDFLARE_TOKEN=your_cloudflare_tunnel_token
```

Replace with:

```
# ── Auth ──────────────────────────────────────────────────────────────────────
# AUTH_PROVIDER selects which auth strategy the platform uses to verify
# incoming requests.
#
# Options:
#   none        — No header check. Auth is handled at the network layer
#                 (e.g. Tailscale, WireGuard, local-only). Safe for home LANs.
#
#   cloudflare  — Validates X-Cloudflare-Access-Identity header injected by
#                 Cloudflare Zero Trust after the user passes email OTP.
#                 Requires cloudflared running on the host and a Zero Trust
#                 application configured at dash.cloudflare.com.
#
# Default: none
AUTH_PROVIDER=none

# ── Cloudflare (if AUTH_PROVIDER=cloudflare) ──────────────────────────────────
# AUTH_PROVIDER=cloudflare
# CLOUDFLARE_TOKEN=your_cloudflare_tunnel_token
```

- [ ] **Step 2: Verify no remaining `TUNNEL_PROVIDER` or `TUNNEL_AUTH_HEADER` references**

```bash
grep -r "TUNNEL_PROVIDER\|TUNNEL_AUTH_HEADER\|tunnel_provider\|tunnel_auth_header" .
```

Expected: no output

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: update .env.example — AUTH_PROVIDER replaces TUNNEL_PROVIDER/TUNNEL_AUTH_HEADER"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] `Identity(email, provider)` frozen dataclass — Task 1
- [x] `AuthProvider` protocol — Task 3
- [x] `NoneAuthProvider` — Task 3
- [x] `CloudflareAuthProvider` — Task 3
- [x] `get_auth_provider()` factory function — Task 3
- [x] `resolve_identity()` top-level function — Task 3
- [x] Config rename `tunnel_provider` → `auth_provider`, remove `tunnel_auth_header` — Task 2
- [x] `_register_hook` updated — Task 4
- [x] `run_app` (MCP) updated — Task 4
- [x] `.env.example` updated — Task 5
- [x] Tests for `Identity` dataclass — Task 1
- [x] Tests for all provider branches and `resolve_identity()` — Task 3

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:**
- `Identity(email: str, provider: str)` — used consistently across all tasks
- `resolve_identity(headers: dict[str, str]) -> Identity` — matches call sites in Tasks 4
- `get_auth_provider(auth_provider: str) -> AuthProvider` — matches test assertions in Task 3
- `AuthProvider.resolve(headers: dict[str, str]) -> Identity` — implemented by both concrete classes

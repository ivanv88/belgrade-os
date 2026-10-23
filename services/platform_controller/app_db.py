"""Per-app Postgres roles (audit finding H3).

Each app logs in as its own role `app_<id>`. What this module enforces:
  * the role is NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOREPLICATION/NOBYPASSRLS (an existing
    role of that name is demoted), with its own login and CONNECTION LIMIT 10;
  * it gets CONNECT and CREATE on the platform database, so the SDK can create
    `app_<tenant>_<app>` schemas on demand; the role owns what it creates and has no access
    to other apps' schemas or platform-only schemas;
  * CREATE on schema `public` is revoked from PUBLIC (clusters upgraded from PG <= 14);
  * passwords are derived from APP_DB_SECRET (nothing new is stored) and are sent to Postgres
    only as a SCRAM-SHA-256 verifier, so failing statements in logs never expose the plaintext.

Accepted residual risks: the role can create TEMP tables and read catalog metadata (e.g. other
apps' table names). Squatting another app's schema name is handled by the SDK ownership check.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

_APP_ID_RE = re.compile(r"^[a-z0-9_]{1,59}$")   # "app_" + 59 = 63, Postgres' identifier limit
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,63}$")
_PASSWORD_RE = re.compile(r"^[0-9a-f]{64}$")
_VERIFIER_RE = re.compile(
    r"^SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$")
_SCRAM_ITERATIONS = 4096


def app_role_name(app_id: str) -> str:
    if not _APP_ID_RE.match(app_id):
        raise ValueError(f"app_id not usable as a DB role: {app_id!r}")
    return "app_" + app_id


def app_role_password(secret: str, app_id: str) -> str:
    if not secret:
        raise ValueError("APP_DB_SECRET is required to derive app DB passwords")
    return hmac.new(secret.encode(), app_id.encode(), hashlib.sha256).hexdigest()


def _scram_sha256_verifier(password: str) -> str:
    """Postgres SCRAM-SHA-256 verifier; deterministic salt keeps ALTER ROLE idempotent."""
    pw = password.encode()
    salt = hmac.new(pw, b"belgrade-os-scram-salt", hashlib.sha256).digest()[:16]
    salted = hashlib.pbkdf2_hmac("sha256", pw, salt, _SCRAM_ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()
    return f"SCRAM-SHA-256${_SCRAM_ITERATIONS}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def app_db_url(platform_url: str, role: str, password: str) -> str:
    url = make_url(platform_url).set(username=role, password=password)
    return url.render_as_string(hide_password=False)


async def ensure_app_role(conn: AsyncConnection, role: str, password: str, database: str) -> None:
    """Create or update the app's role. Identifiers/password are validated, then inlined:
    CREATE/ALTER ROLE and GRANT are utility statements and take no bind parameters."""
    if not role.startswith("app_") or not _DB_NAME_RE.match(role):
        raise ValueError(f"invalid role {role!r}")
    if not _PASSWORD_RE.match(password):
        raise ValueError("password must be a derived 64-char hex digest")
    if not _DB_NAME_RE.match(database):
        raise ValueError(f"invalid database name {database!r}")

    verifier = _scram_sha256_verifier(password)
    if not _VERIFIER_RE.match(verifier):
        raise ValueError("invalid SCRAM verifier")
    quoted_db = conn.dialect.identifier_preparer.quote(database)

    exists = (await conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})).first()
    if not exists:
        await conn.execute(text(f"CREATE ROLE {role}"))
    await conn.execute(text(
        f"ALTER ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
        f"NOBYPASSRLS CONNECTION LIMIT 10 PASSWORD '{verifier}'"
    ))
    await conn.execute(text(f"GRANT CONNECT, CREATE ON DATABASE {quoted_db} TO {role}"))
    await conn.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))

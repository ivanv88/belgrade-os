"""Per-app Postgres roles (audit finding H3).

Each app logs in as its own non-superuser role `app_<id>`. The role may create
schemas in the platform database (the SDK creates `app_<tenant>_<app>` on demand)
and owns what it creates, but has no access to other apps' schemas or `shared`.
Passwords are derived from APP_DB_SECRET, so nothing new is stored.
"""
from __future__ import annotations

import hashlib
import hmac
import re

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

_APP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,59}$")   # "app_" + 59 = 63, Postgres' identifier limit
_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,63}$")
_PASSWORD_RE = re.compile(r"^[0-9a-f]{64}$")


def app_role_name(app_id: str) -> str:
    if not _APP_ID_RE.match(app_id):
        raise ValueError(f"app_id not usable as a DB role: {app_id!r}")
    return "app_" + app_id.replace("-", "_").lower()


def app_role_password(secret: str, app_id: str) -> str:
    if not secret:
        raise ValueError("APP_DB_SECRET is required to derive app DB passwords")
    return hmac.new(secret.encode(), app_id.encode(), hashlib.sha256).hexdigest()


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

    exists = (await conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})).first()
    if not exists:
        await conn.execute(text(f"CREATE ROLE {role}"))
    await conn.execute(text(
        f"ALTER ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
        f"NOBYPASSRLS PASSWORD '{password}'"
    ))
    await conn.execute(text(f"GRANT CONNECT, CREATE ON DATABASE {database} TO {role}"))

from __future__ import annotations
import os
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app_db import app_db_url, app_role_name, app_role_password, ensure_app_role

TEST_DB_URL = os.getenv(
    "BEG_OS_TEST_DB_URL", "postgresql+asyncpg://postgres:test@localhost:5433/belgrade_test"
)


def test_role_name_is_prefixed_and_sql_safe():
    assert app_role_name("shopping") == "app_shopping"
    assert app_role_name("my-app") == "app_my_app"
    for bad in ("x; DROP ROLE postgres", "", "a" * 60):
        with pytest.raises(ValueError):
            app_role_name(bad)


def test_password_is_derived_per_app_and_secret():
    p1 = app_role_password("secret", "shopping")
    assert p1 == app_role_password("secret", "shopping")
    assert p1 != app_role_password("secret", "notes")
    assert p1 != app_role_password("other-secret", "shopping")
    assert len(p1) == 64 and all(c in "0123456789abcdef" for c in p1)


def test_password_requires_a_secret():
    with pytest.raises(ValueError):
        app_role_password("", "shopping")


def test_db_url_swaps_credentials_keeps_host_and_db():
    url = make_url(app_db_url("postgresql+asyncpg://laurent:pw@localhost:5434/belgrade_os", "app_shopping", "abc"))
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "app_shopping", "abc", "localhost", 5434, "belgrade_os")


@pytest.fixture
async def admin_engine():
    eng = create_async_engine(TEST_DB_URL)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"Postgres unreachable at {TEST_DB_URL}: {exc}")
    yield eng
    await eng.dispose()


async def _drop_role(admin_engine, role):
    async with admin_engine.begin() as conn:
        await conn.execute(text(f"DROP OWNED BY {role} CASCADE"))
        await conn.execute(text(f"DROP ROLE IF EXISTS {role}"))


async def test_app_role_is_not_superuser_and_owns_only_its_schemas(admin_engine):
    suffix = uuid.uuid4().hex[:8]
    a, b = f"isoa{suffix}", f"isob{suffix}"
    role_a, role_b = app_role_name(a), app_role_name(b)
    db = make_url(TEST_DB_URL).database
    try:
        for app_id, role in ((a, role_a), (b, role_b)):
            async with admin_engine.begin() as conn:
                await ensure_app_role(conn, role, app_role_password("s", app_id), db)
                await ensure_app_role(conn, role, app_role_password("s", app_id), db)  # idempotent

        async with admin_engine.connect() as conn:
            is_super = (await conn.execute(
                text("SELECT rolsuper FROM pg_roles WHERE rolname = :r"), {"r": role_a})).scalar_one()
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS platform_only"))
            await conn.execute(text("CREATE TABLE IF NOT EXISTS platform_only.secrets (v TEXT)"))
            await conn.commit()
        assert is_super is False

        eng_a = create_async_engine(app_db_url(TEST_DB_URL, role_a, app_role_password("s", a)))
        eng_b = create_async_engine(app_db_url(TEST_DB_URL, role_b, app_role_password("s", b)))
        try:
            async with eng_a.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA app_t_{a}"))
                await conn.execute(text(f"CREATE TABLE app_t_{a}.items (name TEXT)"))
            async with eng_b.connect() as conn:
                with pytest.raises(Exception, match="permission denied"):
                    await conn.execute(text(f"SELECT * FROM app_t_{a}.items"))
            async with eng_a.connect() as conn:
                with pytest.raises(Exception, match="permission denied"):
                    await conn.execute(text("SELECT * FROM platform_only.secrets"))
        finally:
            await eng_a.dispose()
            await eng_b.dispose()
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS platform_only CASCADE"))
        for role in (role_a, role_b):
            await _drop_role(admin_engine, role)


async def test_rotating_the_secret_updates_the_password(admin_engine):
    app_id = f"rot{uuid.uuid4().hex[:8]}"
    role = app_role_name(app_id)
    db = make_url(TEST_DB_URL).database
    try:
        async with admin_engine.begin() as conn:
            await ensure_app_role(conn, role, app_role_password("old", app_id), db)
            await ensure_app_role(conn, role, app_role_password("new", app_id), db)
        eng = create_async_engine(app_db_url(TEST_DB_URL, role, app_role_password("new", app_id)))
        try:
            async with eng.connect() as conn:
                assert (await conn.execute(text("SELECT current_user"))).scalar_one() == role
        finally:
            await eng.dispose()
    finally:
        await _drop_role(admin_engine, role)

from __future__ import annotations
import os
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from belgrade_sdk.context import AppContext

TEST_DB_URL = os.getenv(
    "BEG_OS_TEST_DB_URL", "postgresql+asyncpg://postgres:test@localhost:5433/belgrade_test"
)


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB_URL)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"Postgres unreachable at {TEST_DB_URL}: {exc}")
    yield eng
    await eng.dispose()


def _ctx(engine, tenant_id: str) -> AppContext:
    return AppContext(
        app_id="isotest", user_id="u1", tenant_id=tenant_id,
        trace_id="t", bridge_url="http://bridge", db_engine=engine,
    )


async def test_tables_land_in_tenant_schema_not_public(engine):
    """A fresh tenant's tables must be created in its own schema, never in shared public."""
    tenant = f"t{uuid.uuid4().hex[:8]}"
    ctx = _ctx(engine, tenant)
    try:
        db = await ctx.db
        await db.execute(text("CREATE TABLE items (name TEXT)"))
        await db.commit()
        row = (await db.execute(text(
            "SELECT table_schema FROM information_schema.tables WHERE table_name = 'items' "
            "AND table_schema = current_schema()"
        ))).scalar_one()
        assert row == f"app_{tenant}_isotest"
    finally:
        await ctx.cleanup()
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS app_{tenant}_isotest CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS public.items"))


async def test_tenants_cannot_see_each_others_rows(engine):
    a, b = (f"t{uuid.uuid4().hex[:8]}" for _ in range(2))
    try:
        for tenant, value in ((a, "milk"), (b, "eggs")):
            ctx = _ctx(engine, tenant)
            db = await ctx.db
            await db.execute(text("CREATE TABLE items (name TEXT)"))
            await db.execute(text("INSERT INTO items VALUES (:v)"), {"v": value})
            await db.commit()
            await ctx.cleanup()

        ctx = _ctx(engine, a)
        db = await ctx.db
        names = (await db.execute(text("SELECT name FROM items"))).scalars().all()
        await ctx.cleanup()
        assert names == ["milk"]
    finally:
        async with engine.begin() as conn:
            for tenant in (a, b):
                await conn.execute(text(f"DROP SCHEMA IF EXISTS app_{tenant}_isotest CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS public.items"))


async def test_rejects_tenant_id_unsafe_for_sql_identifier():
    """tenant_id is interpolated into a schema name — anything but [A-Za-z0-9_-] must be refused."""
    ctx = AppContext(
        app_id="isotest", user_id="u1", tenant_id="x; DROP SCHEMA public CASCADE --",
        trace_id="t", bridge_url="http://bridge", db_engine=object(),
    )
    with pytest.raises(ValueError):
        await ctx.db


async def test_schema_survives_commit_when_pool_hands_connection_to_another_tenant():
    """search_path is per-connection; after commit the pool may give this session a connection
    another tenant configured. With a FIFO pool, an unpinned A would release its connection on
    commit, B would reuse it, and A would get it back pointing at B's schema."""
    eng = create_async_engine(TEST_DB_URL, pool_size=2, max_overflow=0, pool_timeout=5)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"Postgres unreachable at {TEST_DB_URL}: {exc}")

    a, b = (f"t{uuid.uuid4().hex[:8]}" for _ in range(2))
    try:
        ctx_a = _ctx(eng, a)
        db_a = await ctx_a.db
        await db_a.execute(text("CREATE TABLE items (name TEXT)"))
        await db_a.commit()  # A releases its connection back to the pool

        ctx_b = _ctx(eng, b)  # B picks up that connection and points it at B's schema
        db_b = await ctx_b.db
        await db_b.execute(text("CREATE TABLE items (name TEXT)"))
        await db_b.commit()
        await ctx_b.cleanup()

        await db_a.execute(text("INSERT INTO items VALUES ('milk')"))  # A continues
        await db_a.commit()
        await ctx_a.cleanup()

        async with eng.connect() as conn:
            b_rows = (await conn.execute(text(f"SELECT count(*) FROM app_{b}_isotest.items"))).scalar_one()
            a_rows = (await conn.execute(text(f"SELECT count(*) FROM app_{a}_isotest.items"))).scalar_one()
        assert (a_rows, b_rows) == (1, 0)
    finally:
        async with eng.begin() as conn:
            for tenant in (a, b):
                await conn.execute(text(f"DROP SCHEMA IF EXISTS app_{tenant}_isotest CASCADE"))
        await eng.dispose()


async def test_tenantless_context_does_not_inherit_previous_tenant_schema():
    eng = create_async_engine(TEST_DB_URL, pool_size=1, max_overflow=0, pool_timeout=5)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"Postgres unreachable at {TEST_DB_URL}: {exc}")

    a = f"t{uuid.uuid4().hex[:8]}"
    try:
        ctx_a = _ctx(eng, a)
        db_a = await ctx_a.db
        await db_a.execute(text("SELECT 1"))
        await db_a.commit()  # a committed SET outlives the transaction on the pooled connection
        await ctx_a.cleanup()

        ctx_none = _ctx(eng, None)
        schema = (await (await ctx_none.db).execute(text("SELECT current_schema()"))).scalar_one()
        await ctx_none.cleanup()
        assert schema == "public"
    finally:
        async with eng.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS app_{a}_isotest CASCADE"))
        await eng.dispose()

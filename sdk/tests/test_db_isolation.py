from __future__ import annotations
import asyncio
import os
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
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


async def test_refuses_schema_owned_by_another_role(engine):
    """A schema squatted by another app's role must not be used — its owner could read our rows."""
    tenant = f"t{uuid.uuid4().hex[:8]}"
    squatter = f"squat{uuid.uuid4().hex[:8]}"
    schema = f"app_{tenant}_isotest"
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE ROLE {squatter}"))
            await conn.execute(text(f"CREATE SCHEMA {schema} AUTHORIZATION {squatter}"))
        ctx = _ctx(engine, tenant)
        with pytest.raises(PermissionError, match=schema):
            await ctx.db
        await ctx.cleanup()
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {squatter}"))


async def test_refused_schema_does_not_leak_pooled_connection():
    """After a refused (squatted) schema, cleanup() must return the connection so the pool is usable."""
    eng = create_async_engine(TEST_DB_URL, pool_size=1, max_overflow=0, pool_timeout=5)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"Postgres unreachable at {TEST_DB_URL}: {exc}")

    bad, good = (f"t{uuid.uuid4().hex[:8]}" for _ in range(2))
    squatter = f"squat{uuid.uuid4().hex[:8]}"
    try:
        async with eng.begin() as conn:
            await conn.execute(text(f"CREATE ROLE {squatter}"))
            await conn.execute(text(f"CREATE SCHEMA app_{bad}_isotest AUTHORIZATION {squatter}"))
        ctx_bad = _ctx(eng, bad)
        with pytest.raises(PermissionError):
            await ctx_bad.db
        await ctx_bad.cleanup()

        ctx_good = _ctx(eng, good)
        schema = (await (await ctx_good.db).execute(text("SELECT current_schema()"))).scalar_one()
        await ctx_good.cleanup()
        assert schema == f"app_{good}_isotest"
    finally:
        async with eng.begin() as conn:
            for t in (bad, good):
                await conn.execute(text(f"DROP SCHEMA IF EXISTS app_{t}_isotest CASCADE"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {squatter}"))
        await eng.dispose()


def _ctx_for(tenant, app) -> AppContext:
    return AppContext(
        app_id=app, user_id="u1", tenant_id=tenant,
        trace_id="t", bridge_url="http://bridge", db_engine=object(),
    )


@pytest.mark.parametrize("tenant,app", [
    ("a-b", "shopping"),        # '-' used to collapse onto '_'
    ("a_b", "shopping"),        # '_' makes the tenant/app boundary ambiguous
    ("Family", "shopping"),     # Postgres folds unquoted identifiers to lowercase
    ("family", "My-App"),
    ("family", "Shopping"),
    ("a" * 33, "shopping"),
    ("family", "x" * 60),
    ("t" * 32, "x" * 59),       # each part valid, schema name exceeds 63 chars
])
async def test_rejects_ids_that_cannot_map_one_to_one_to_a_schema(tenant, app):
    with pytest.raises(ValueError):
        await _ctx_for(tenant, app).db


async def test_family_shopping_schema_name_still_valid(engine):
    ctx = _ctx(engine, "family")
    ctx.app_id = "shopping"
    try:
        schema = (await (await ctx.db).execute(text("SELECT current_schema()"))).scalar_one()
        assert schema == "app_family_shopping"
    finally:
        await ctx.cleanup()
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS app_family_shopping CASCADE"))


async def test_concurrent_first_use_of_new_tenant_all_succeed():
    eng = create_async_engine(TEST_DB_URL, pool_size=8, max_overflow=0, pool_timeout=5)
    try:
        # Pre-warm all 8 pooled connections so the contexts' CREATEs overlap instead of
        # being staggered by connection setup (which hides the race).
        warm = [await eng.connect() for _ in range(8)]
        for c in warm:
            await c.close()
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"Postgres unreachable at {TEST_DB_URL}: {exc}")

    tenants = [f"t{uuid.uuid4().hex[:8]}" for _ in range(5)]
    ctxs: list[AppContext] = []
    try:
        for tenant in tenants:  # several rounds, each a brand-new tenant
            round_ctxs = [_ctx(eng, tenant) for _ in range(8)]
            ctxs.extend(round_ctxs)
            results = await asyncio.gather(*(c.db for c in round_ctxs), return_exceptions=True)
            assert [r for r in results if isinstance(r, BaseException)] == []
            for c in round_ctxs:
                await c.cleanup()
    finally:
        for c in ctxs:
            await c.cleanup()
        async with eng.begin() as conn:
            for tenant in tenants:
                await conn.execute(text(f"DROP SCHEMA IF EXISTS app_{tenant}_isotest CASCADE"))
        await eng.dispose()


async def test_retrying_refused_db_on_same_context_does_not_orphan_connection():
    eng = create_async_engine(TEST_DB_URL, pool_size=1, max_overflow=0, pool_timeout=5)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"Postgres unreachable at {TEST_DB_URL}: {exc}")

    tenant = f"t{uuid.uuid4().hex[:8]}"
    squatter = f"squat{uuid.uuid4().hex[:8]}"
    schema = f"app_{tenant}_isotest"
    try:
        async with eng.begin() as conn:
            await conn.execute(text(f"CREATE ROLE {squatter}"))
            await conn.execute(text(f"CREATE SCHEMA {schema} AUTHORIZATION {squatter}"))
        ctx = _ctx(eng, tenant)
        with pytest.raises(PermissionError):
            await ctx.db
        with pytest.raises(PermissionError):  # not a pool TimeoutError
            await ctx.db
        await ctx.cleanup()
    finally:
        async with eng.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {squatter}"))
        await eng.dispose()


async def test_refusal_message_names_the_owner(engine):
    tenant = f"t{uuid.uuid4().hex[:8]}"
    squatter = f"squat{uuid.uuid4().hex[:8]}"
    schema = f"app_{tenant}_isotest"
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE ROLE {squatter}"))
            await conn.execute(text(f"CREATE SCHEMA {schema} AUTHORIZATION {squatter}"))
        ctx = _ctx(engine, tenant)
        with pytest.raises(PermissionError, match=f"owned by role {squatter}, not postgres"):
            await ctx.db
        await ctx.cleanup()
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {squatter}"))


async def test_works_as_real_non_superuser_role(engine):
    """The ownership check must pass for the role that actually creates the schema."""
    role = f"nosu{uuid.uuid4().hex[:8]}"
    tenant = f"t{uuid.uuid4().hex[:8]}"
    schema = f"app_{tenant}_isotest"
    dbname = make_url(TEST_DB_URL).database
    role_eng = None
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE ROLE {role} LOGIN NOSUPERUSER PASSWORD 'pw'"))
            await conn.execute(text(f"GRANT CONNECT, CREATE ON DATABASE {dbname} TO {role}"))
        role_eng = create_async_engine(
            make_url(TEST_DB_URL).set(username=role, password="pw"), pool_size=1, max_overflow=0)
        ctx = _ctx(role_eng, tenant)
        try:
            db = await ctx.db
            assert (await db.execute(text("SELECT current_schema()"))).scalar_one() == schema
        finally:
            await ctx.cleanup()
        async with engine.connect() as conn:
            owner = (await conn.execute(text(
                "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = :s"),
                {"s": schema})).scalar_one()
        assert owner == role
    finally:
        if role_eng is not None:
            await role_eng.dispose()
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            exists = (await conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})).first()
            if exists:
                await conn.execute(text(f"DROP OWNED BY {role}"))
                await conn.execute(text(f"DROP ROLE {role}"))

from __future__ import annotations
import json
import os
import uuid
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from main import app as shopping

TEST_DB_URL = os.getenv(
    "BEG_OS_TEST_DB_URL", "postgresql+asyncpg://postgres:test@localhost:5433/belgrade_test"
)


@pytest.fixture
async def call():
    """Invoke tools through the real /execute endpoint against a fresh tenant schema."""
    engine = create_async_engine(TEST_DB_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Postgres unreachable at {TEST_DB_URL}: {exc}")

    tenant = f"t{uuid.uuid4().hex[:8]}"
    shopping._db_engine = engine
    transport = httpx.ASGITransport(app=shopping.app)

    async def _call(tool: str, user_id: str = "ana", **kwargs):
        async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
            resp = await client.post("/execute", json={
                "tool_name": f"shopping:{tool}",
                "input_json": json.dumps(kwargs),
                "user_id": user_id,
                "tenant_id": tenant,
                "trace_id": "t",
            })
        body = resp.json()
        assert body["success"], body.get("error")
        return json.loads(body["output_json"])

    yield _call

    shopping._db_engine = None
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS app_{tenant}_shopping CASCADE"))
    await engine.dispose()


def _names(listing: dict) -> list[str]:
    return [i["name"] for i in listing["items"]]


async def test_added_items_are_listed(call):
    await call("add_item", name="milk", quantity="2")
    await call("add_item", name="eggs")

    listing = await call("list_items")

    assert _names(listing) == ["milk", "eggs"]
    assert listing["items"][0]["quantity"] == "2"
    assert listing["items"][1]["quantity"] == "1"


async def test_adding_an_item_already_on_the_list_updates_it_instead_of_duplicating(call):
    await call("add_item", name="Milk")
    await call("add_item", name="milk", quantity="3")

    listing = await call("list_items")

    assert len(listing["items"]) == 1
    assert listing["items"][0]["quantity"] == "3"


async def test_checked_items_are_hidden_unless_requested(call):
    await call("add_item", name="milk")
    await call("add_item", name="eggs")

    await call("check_item", item="MILK")

    assert _names(await call("list_items")) == ["eggs"]
    everything = await call("list_items", include_checked="true")
    assert {i["name"]: i["checked"] for i in everything["items"]} == {"milk": True, "eggs": False}


async def test_check_item_accepts_the_id_from_list_items(call):
    added = await call("add_item", name="bread")

    await call("check_item", item=str(added["id"]))

    assert _names(await call("list_items")) == []


async def test_checking_an_unknown_item_reports_not_found(call):
    result = await call("check_item", item="caviar")

    assert result == {"status": "not_found", "item": "caviar"}


async def test_remove_item_deletes_it(call):
    await call("add_item", name="milk")
    await call("add_item", name="eggs")

    await call("remove_item", item="milk")

    assert _names(await call("list_items", include_checked="true")) == ["eggs"]


async def test_clear_checked_removes_only_checked_items(call):
    for name in ("milk", "eggs", "bread"):
        await call("add_item", name=name)
    await call("check_item", item="milk")
    await call("check_item", item="bread")

    result = await call("clear_checked")

    assert result == {"status": "cleared", "removed": 2}
    assert _names(await call("list_items", include_checked="true")) == ["eggs"]


async def test_list_is_shared_by_the_household(call):
    """One list per tenant (family), not per user — anyone can see what others added."""
    await call("add_item", user_id="ana", name="milk")

    listing = await call("list_items", user_id="marko")

    assert listing["items"][0]["name"] == "milk"
    assert listing["items"][0]["added_by"] == "ana"


async def test_rejects_blank_item_name(call):
    with pytest.raises(AssertionError, match="name"):
        await call("add_item", name="   ")

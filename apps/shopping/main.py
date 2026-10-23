# belgrade_sdk is on PYTHONPATH — the Platform Controller provides it.
from belgrade_sdk.app import BelgradeApp
from belgrade_sdk.context import AppContext

import store

app = BelgradeApp(app_id="shopping")


async def _db(ctx: AppContext):
    db = await ctx.db
    await store.ensure_schema(db)
    return db


@app.tool(
    name="add_item",
    description="Add an item to the household shopping list",
    mcp_hint="If the item is already on the list, its quantity is updated instead of adding a duplicate",
)
async def add_item(ctx: AppContext, name: str, quantity: str = "1"):
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    db = await _db(ctx)
    item = await store.add(db, name, quantity.strip() or "1", ctx.user_id or "")
    await db.commit()
    return item


@app.tool(
    name="list_items",
    description="List what's on the household shopping list",
    mcp_hint="Pass include_checked='true' to also show items already bought",
)
async def list_items(ctx: AppContext, include_checked: str = "false"):
    db = await _db(ctx)
    return {"items": await store.list_items(db, include_checked.lower() == "true")}


@app.tool(
    name="check_item",
    description="Mark an item on the shopping list as bought",
    mcp_hint="item is the item's name or its id from list_items",
)
async def check_item(ctx: AppContext, item: str):
    db = await _db(ctx)
    checked = await store.check(db, item.strip())
    await db.commit()
    return checked or {"status": "not_found", "item": item}


@app.tool(
    name="remove_item",
    description="Remove an item from the shopping list entirely",
    mcp_hint="item is the item's name or its id from list_items",
)
async def remove_item(ctx: AppContext, item: str):
    db = await _db(ctx)
    removed = await store.remove(db, item.strip())
    await db.commit()
    return removed or {"status": "not_found", "item": item}


@app.tool(
    name="clear_checked",
    description="Remove all items already marked as bought from the shopping list",
)
async def clear_checked(ctx: AppContext):
    db = await _db(ctx)
    removed = await store.clear_checked(db)
    await db.commit()
    return {"status": "cleared", "removed": removed}


if __name__ == "__main__":
    # Port is assigned by the Platform Controller (BEG_OS_PORT); 9001 is for standalone runs.
    app.run(port=9001)

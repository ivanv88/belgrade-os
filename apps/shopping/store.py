"""Shopping list persistence. One list per tenant (household), stored in the
tenant's app schema — ctx.db already sets search_path, so names here are unqualified."""
from __future__ import annotations
from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    quantity   TEXT NOT NULL DEFAULT '1',
    checked    BOOLEAN NOT NULL DEFAULT FALSE,
    added_by   TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_COLUMNS = "id, name, quantity, checked, added_by"


async def ensure_schema(db: AsyncSession) -> None:
    await db.execute(text(_SCHEMA))


def _row(r) -> dict:
    return {"id": r.id, "name": r.name, "quantity": r.quantity, "checked": r.checked, "added_by": r.added_by}


async def add(db: AsyncSession, name: str, quantity: str, added_by: str) -> dict:
    """Add an item, or update the quantity if it's already on the list (unchecked)."""
    existing = (await db.execute(
        text(f"UPDATE items SET quantity = :q WHERE lower(name) = lower(:n) AND NOT checked RETURNING {_COLUMNS}"),
        {"q": quantity, "n": name},
    )).first()
    if existing:
        return _row(existing)
    created = (await db.execute(
        text(f"INSERT INTO items (name, quantity, added_by) VALUES (:n, :q, :by) RETURNING {_COLUMNS}"),
        {"n": name, "q": quantity, "by": added_by},
    )).one()
    return _row(created)


async def list_items(db: AsyncSession, include_checked: bool) -> list[dict]:
    where = "" if include_checked else "WHERE NOT checked"
    rows = await db.execute(text(f"SELECT {_COLUMNS} FROM items {where} ORDER BY id"))
    return [_row(r) for r in rows]


def _match(item: str) -> tuple[str, dict]:
    """`item` is either an id from list_items or a name (case-insensitive)."""
    if item.isdigit():
        return "id = :id", {"id": int(item)}
    return "lower(name) = lower(:n)", {"n": item}


async def check(db: AsyncSession, item: str) -> Optional[dict]:
    cond, params = _match(item)
    row = (await db.execute(
        text(f"UPDATE items SET checked = TRUE WHERE id = ("
             f"SELECT id FROM items WHERE {cond} AND NOT checked ORDER BY id LIMIT 1) RETURNING {_COLUMNS}"),
        params,
    )).first()
    return _row(row) if row else None


async def remove(db: AsyncSession, item: str) -> Optional[dict]:
    cond, params = _match(item)
    row = (await db.execute(
        text(f"DELETE FROM items WHERE id = (SELECT id FROM items WHERE {cond} ORDER BY checked, id LIMIT 1) "
             f"RETURNING {_COLUMNS}"),
        params,
    )).first()
    return _row(row) if row else None


async def clear_checked(db: AsyncSession) -> int:
    result = await db.execute(text("DELETE FROM items WHERE checked"))
    return result.rowcount

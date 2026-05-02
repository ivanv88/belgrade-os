import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DB_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")

async def seed():
    engine = create_async_engine(DB_URL)
    async with engine.begin() as conn:
        print("Seeding permissions...")
        await conn.execute(text("""
            INSERT INTO shared.app_permissions (user_id, app_id, bundle_id, role)
            VALUES ('ivan@example.com', 'shopping', 'web', 'admin')
            ON CONFLICT (user_id, app_id, bundle_id) DO UPDATE SET role = EXCLUDED.role
        """))
        await conn.execute(text("""
            INSERT INTO shared.app_permissions (user_id, app_id, bundle_id, role)
            VALUES ('ivan@example.com', 'shopping', 'mobile', 'admin')
            ON CONFLICT (user_id, app_id, bundle_id) DO UPDATE SET role = EXCLUDED.role
        """))
        print("Done.")

if __name__ == "__main__":
    asyncio.run(seed())

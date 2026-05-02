import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DB_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")

async def seed():
    engine = create_async_engine(DB_URL)
    async with engine.begin() as conn:
        print("Seeding permissions...")
        users = ['ivan@example.com']
        apps = [
            ('shopping', 'web', 'admin'),
            ('shopping', 'mobile', 'admin'),
            ('demo_app', 'web', 'admin'),
            ('dashboard', 'web', 'admin'),
        ]
        
        for user in users:
            for app_id, bundle, role in apps:
                await conn.execute(text("""
                    INSERT INTO shared.app_permissions (user_id, app_id, bundle_id, role)
                    VALUES (:user, :app, :bundle, :role)
                    ON CONFLICT (user_id, app_id, bundle_id) DO UPDATE SET role = EXCLUDED.role
                """), {"user": user, "app": app_id, "bundle": bundle, "role": role})
        print("Done.")

if __name__ == "__main__":
    asyncio.run(seed())

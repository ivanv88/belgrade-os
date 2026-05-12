import asyncio
import json
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

DB_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
APPS_DIR = Path(__file__).parent.parent / "apps"


def discover_apps() -> list[tuple[str, str]]:
    """Return (app_id, bundle_id) pairs by scanning apps/*/manifest.json."""
    pairs = []
    for manifest_path in sorted(APPS_DIR.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text())
        app_id = manifest["app_id"]
        for bundle in manifest.get("ui", {}).get("bundles", {}):
            pairs.append((app_id, bundle))
    return pairs


async def seed():
    engine = create_async_engine(DB_URL)
    async with engine.begin() as conn:
        users = ["ivan@example.com"]
        apps = discover_apps()
        print(f"Seeding {len(users)} user(s) × {len(apps)} app/bundle pairs...")
        for user in users:
            for app_id, bundle in apps:
                await conn.execute(
                    text("""
                        INSERT INTO shared.app_permissions (user_id, app_id, bundle_id, role)
                        VALUES (:user, :app, :bundle, :role)
                        ON CONFLICT (user_id, app_id, bundle_id) DO UPDATE SET role = EXCLUDED.role
                    """),
                    {"user": user, "app": app_id, "bundle": bundle, "role": "admin"},
                )
        print("Done.")


if __name__ == "__main__":
    asyncio.run(seed())

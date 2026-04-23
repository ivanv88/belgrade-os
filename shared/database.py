from __future__ import annotations
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, AsyncEngine, async_sessionmaker
from sqlmodel import SQLModel
from dotenv import load_dotenv

load_dotenv()

SHARED_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS shared;

CREATE TABLE IF NOT EXISTS shared.config (
    namespace   VARCHAR NOT NULL,
    data        JSONB NOT NULL,
    updated_at  TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (namespace, updated_at)
);

CREATE INDEX IF NOT EXISTS idx_shared_config_data
    ON shared.config USING GIN (data);

CREATE OR REPLACE VIEW shared.current_metrics AS
SELECT DISTINCT ON (namespace) namespace, data, updated_at
FROM shared.config
ORDER BY namespace, updated_at DESC;
"""


def build_database_url(user: str, password: str, host: str, port: int, db: str) -> str:
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def get_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, echo=False)


DB_USER = os.getenv("DB_USER", "laurent")
DB_PASS = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "belgrade_os")

DATABASE_URL = build_database_url(DB_USER, DB_PASS, DB_HOST, DB_PORT, DB_NAME)
engine = get_engine(DATABASE_URL)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db() -> None:
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.execute(text(SHARED_SCHEMA_SQL))


def get_session() -> AsyncSession:
    return AsyncSessionLocal()

import pytest
from unittest.mock import patch, MagicMock
from shared.database import get_engine, get_session, SHARED_SCHEMA_SQL, build_database_url


def test_database_url_format() -> None:
    url = build_database_url("user", "pass", "localhost", 5432, "mydb")
    assert url == "postgresql+asyncpg://user:pass@localhost:5432/mydb"


def test_shared_schema_sql_contains_required_statements() -> None:
    assert "CREATE SCHEMA IF NOT EXISTS shared" in SHARED_SCHEMA_SQL
    assert "shared.config" in SHARED_SCHEMA_SQL
    assert "shared.current_metrics" in SHARED_SCHEMA_SQL
    assert "GIN" in SHARED_SCHEMA_SQL


def test_get_engine_returns_engine() -> None:
    engine = get_engine("postgresql+asyncpg://user:pass@localhost/db")
    assert engine is not None
    assert "asyncpg" in str(engine.url)

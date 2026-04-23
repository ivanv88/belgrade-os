import pytest
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any


@pytest.fixture
def tmp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def apps_dir(tmp_dir: Path) -> Path:
    apps = tmp_dir / "apps"
    apps.mkdir()
    return apps


@pytest.fixture
def sample_manifest_dict() -> dict[str, Any]:
    return {
        "app_id": "test-app",
        "name": "Test App",
        "description": "A test application",
        "version": "1.0.0",
        "pattern": "worker",
        "mcp_enabled": False,
        "triggers": [{"type": "cron", "schedule": "0 20 * * *"}],
        "storage": {"scope": "test-app", "adapter": "local"},
        "config": {},
    }

import pytest
import tempfile
from pathlib import Path


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def apps_dir(tmp_dir):
    apps = tmp_dir / "apps"
    apps.mkdir()
    return apps


@pytest.fixture
def sample_manifest_dict():
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

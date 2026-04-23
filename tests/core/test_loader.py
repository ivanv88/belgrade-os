import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from core.loader import Bootstrapper


def make_app_dir(apps_dir: Path, app_id: str, manifest: dict) -> Path:
    app_path = apps_dir / app_id
    app_path.mkdir()
    (app_path / "manifest.json").write_text(json.dumps(manifest))
    (app_path / "main.py").write_text("async def execute(ctx): pass")
    return app_path


def test_bootstrapper_discovers_manifests(apps_dir: Path, sample_manifest_dict: dict) -> None:
    make_app_dir(apps_dir, "test-app", sample_manifest_dict)
    boot = Bootstrapper(apps_dir=apps_dir, data_dir=apps_dir.parent / "data")
    manifests = boot.discover()
    assert len(manifests) == 1
    assert manifests[0].app_id == "test-app"


def test_bootstrapper_skips_invalid_manifest(apps_dir: Path) -> None:
    app_path = apps_dir / "bad-app"
    app_path.mkdir()
    (app_path / "manifest.json").write_text('{"invalid": true}')
    boot = Bootstrapper(apps_dir=apps_dir, data_dir=apps_dir.parent / "data")
    manifests = boot.discover()
    assert len(manifests) == 0


def test_bootstrapper_skips_dir_without_manifest(apps_dir: Path) -> None:
    (apps_dir / "no-manifest").mkdir()
    boot = Bootstrapper(apps_dir=apps_dir, data_dir=apps_dir.parent / "data")
    manifests = boot.discover()
    assert len(manifests) == 0


async def test_bootstrapper_provisions_data_dir(apps_dir: Path, sample_manifest_dict: dict, tmp_dir: Path) -> None:
    make_app_dir(apps_dir, "test-app", sample_manifest_dict)
    data_dir = tmp_dir / "data" / "apps"
    boot = Bootstrapper(apps_dir=apps_dir, data_dir=data_dir)

    with patch.object(boot, "_provision_db_schema", new_callable=AsyncMock):
        await boot.provision("test-app")

    assert (data_dir / "test-app").exists()


def test_bootstrapper_builds_subscription_map(apps_dir: Path, sample_manifest_dict: dict, tmp_dir: Path) -> None:
    manifest = {**sample_manifest_dict, "triggers": [{"type": "event", "topic": "nutrition.goal_reached"}]}
    make_app_dir(apps_dir, "test-app", manifest)
    boot = Bootstrapper(apps_dir=apps_dir, data_dir=tmp_dir)
    manifests = boot.discover()
    boot.build_subscription_map(manifests)
    assert "test-app" in boot.subscription_map.get("nutrition.goal_reached", [])

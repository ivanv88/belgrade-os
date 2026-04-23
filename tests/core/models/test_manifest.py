# tests/core/models/test_manifest.py
import pytest
from pydantic import ValidationError
from core.models.manifest import AppManifest, AppTrigger, AppStorage, AppConfig


def test_valid_manifest(sample_manifest_dict):
    manifest = AppManifest.model_validate(sample_manifest_dict)
    assert manifest.app_id == "test-app"
    assert manifest.pattern == "worker"
    assert len(manifest.triggers) == 1
    assert manifest.triggers[0].type == "cron"
    assert manifest.triggers[0].schedule == "0 20 * * *"


def test_invalid_app_id_uppercase():
    with pytest.raises(ValidationError, match="app_id"):
        AppManifest.model_validate({
            "app_id": "TestApp",
            "name": "Test", "description": "d", "version": "1.0.0",
            "pattern": "worker", "triggers": [],
            "storage": {"scope": "test"},
        })


def test_invalid_app_id_spaces():
    with pytest.raises(ValidationError):
        AppManifest.model_validate({
            "app_id": "test app",
            "name": "Test", "description": "d", "version": "1.0.0",
            "pattern": "worker", "triggers": [],
            "storage": {"scope": "test"},
        })


def test_event_trigger_requires_namespaced_topic():
    with pytest.raises(ValidationError, match="topic"):
        AppTrigger.model_validate({"type": "event", "topic": "unnamespaced"})


def test_event_trigger_valid_topic():
    trigger = AppTrigger.model_validate({"type": "event", "topic": "nutrition.goal_reached"})
    assert trigger.topic == "nutrition.goal_reached"


def test_cron_trigger_requires_schedule():
    with pytest.raises(ValidationError, match="schedule"):
        AppTrigger.model_validate({"type": "cron"})


def test_observer_trigger_requires_path():
    with pytest.raises(ValidationError, match="path"):
        AppTrigger.model_validate({"type": "observer"})


def test_storage_defaults_to_local():
    storage = AppStorage.model_validate({"scope": "myapp"})
    assert storage.adapter == "local"


def test_shared_write_defaults_false():
    config = AppConfig.model_validate({})
    assert config.shared_write is False

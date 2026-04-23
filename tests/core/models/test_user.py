import json
import pytest
from pathlib import Path
from pydantic import ValidationError
from core.models.user import User, load_identity


def test_user_model() -> None:
    user = User.model_validate({
        "name": "Laurent",
        "email": "laurent@example.com",
    })
    assert user.name == "Laurent"
    assert user.timezone == "Europe/Belgrade"
    assert user.locale == "sr-RS"


def test_user_requires_name_and_email() -> None:
    with pytest.raises(ValidationError):
        User.model_validate({"name": "Laurent"})


def test_load_identity(tmp_dir: Path) -> None:
    identity_file = tmp_dir / "identity.json"
    identity_file.write_text(json.dumps({
        "name": "Laurent",
        "email": "laurent@example.com",
        "timezone": "Europe/Belgrade",
    }))
    user = load_identity(identity_file)
    assert user.name == "Laurent"


def test_load_identity_missing_file(tmp_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_identity(tmp_dir / "missing.json")

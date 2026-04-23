from __future__ import annotations
import json
from pathlib import Path
from pydantic import BaseModel


class User(BaseModel):
    name: str
    email: str
    timezone: str = "Europe/Belgrade"
    locale: str = "sr-RS"


def load_identity(path: Path) -> User:
    if not path.exists():
        raise FileNotFoundError(f"identity.json not found at {path}")
    return User.model_validate(json.loads(path.read_text()))

from __future__ import annotations
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path
from fastapi import FastAPI
from core.loader import Bootstrapper
from core.eventbus import EventBus
from shared.database import init_db


event_bus = EventBus()
bootstrapper = Bootstrapper(
    apps_dir=Path("apps"),
    data_dir=Path("data/apps"),
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    await bootstrapper.bootstrap(app, event_bus)
    yield


app = FastAPI(title="Belgrade AI OS", lifespan=lifespan)


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "Zdravo, Laurent!", "status": "running"}


@app.get("/health")
async def health() -> dict[str, object]:
    return {"status": "ok", "apps": len(bootstrapper._manifests)}

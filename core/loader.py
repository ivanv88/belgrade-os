from __future__ import annotations
import importlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import text

from core.context import AppContext, AppMeta
from core.eventbus import EventBus
from core.executor import safe_execute
from core.io import LocalAdapter
from core.models.manifest import AppManifest
from core.models.user import User, load_identity
from shared.database import engine, AsyncSessionLocal
from shared.ntfy import NotifyService

logger = logging.getLogger(__name__)


class Bootstrapper:
    def __init__(self, apps_dir: Path, data_dir: Path) -> None:
        self.apps_dir = apps_dir
        self.data_dir = data_dir
        self.subscription_map: Dict[str, List[str]] = {}
        self._manifests: List[AppManifest] = []

    def discover(self) -> List[AppManifest]:
        manifests: List[AppManifest] = []
        for item in self.apps_dir.iterdir():
            if not item.is_dir():
                continue
            manifest_path = item / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                data = json.loads(manifest_path.read_text())
                manifests.append(AppManifest.model_validate(data))
            except (ValidationError, json.JSONDecodeError) as e:
                logger.warning("Skipping %s: %s", item.name, e)
        self._manifests = manifests
        return manifests

    def build_subscription_map(self, manifests: List[AppManifest]) -> None:
        for manifest in manifests:
            for trigger in manifest.triggers:
                if trigger.type == "event" and trigger.topic:
                    self.subscription_map.setdefault(trigger.topic, []).append(manifest.app_id)

    async def _provision_db_schema(self, app_id: str) -> None:
        safe_id = app_id.replace("-", "_")
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS app_{safe_id}"))

    async def provision(self, app_id: str) -> None:
        app_data_dir = self.data_dir / app_id
        app_data_dir.mkdir(parents=True, exist_ok=True)
        await self._provision_db_schema(app_id)

    def _register_app_schemas(self, app_id: str, event_bus: EventBus) -> None:
        try:
            mod = importlib.import_module(f"apps.{app_id}.events")
            for topic, schema in getattr(mod, "EVENT_SCHEMAS", {}).items():
                event_bus.register_schema(topic, schema)
                logger.info("Registered schema for %s", topic)
        except ImportError:
            pass

    def _load_metrics_schema(self, app_id: str) -> Optional[Any]:
        try:
            mod = importlib.import_module(f"apps.{app_id}.metrics")
            return getattr(mod, "MetricsSchema", None)
        except ImportError:
            return None

    def build_ctx_factory(
        self,
        manifest: AppManifest,
        user: User,
        event_bus: EventBus,
        notify: NotifyService,
    ) -> Callable[[], Any]:
        base_path = self.data_dir / manifest.app_id
        metrics_schema = self._load_metrics_schema(manifest.app_id)

        async def factory() -> AppContext:
            session = AsyncSessionLocal()
            io = LocalAdapter(base_path)
            metrics: Any = {}
            if metrics_schema:
                async with engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT data FROM shared.current_metrics"
                            " WHERE namespace = 'user.metrics'"
                        )
                    )
                    row = result.fetchone()
                    if row:
                        metrics = metrics_schema.model_validate(dict(row[0]))
            return AppContext(
                user=user,
                metrics=metrics,
                db=session,
                io=io,
                event_bus=event_bus,
                notify=notify,
                meta=AppMeta(
                    app_id=manifest.app_id,
                    timestamp=datetime.now(),
                    secrets={},
                ),
            )

        return factory

    async def bootstrap(self, app: FastAPI, event_bus: EventBus) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        user = load_identity(Path("identity.json"))
        ntfy_topic: Optional[str] = None
        try:
            from core.config import settings
            ntfy_topic = getattr(settings, "ntfy_topic", None)
        except Exception:
            pass
        notify = NotifyService(topic=ntfy_topic)
        scheduler = AsyncIOScheduler()
        manifests = self.discover()
        self.build_subscription_map(manifests)

        for manifest in manifests:
            await self.provision(manifest.app_id)
            self._register_app_schemas(manifest.app_id, event_bus)

            for topic, subscribers in self.subscription_map.items():
                for sub_id in subscribers:
                    event_bus.register_subscription(topic, sub_id)

            ctx_factory = self.build_ctx_factory(manifest, user, event_bus, notify)

            for trigger in manifest.triggers:
                if trigger.type == "cron" and trigger.schedule:
                    async def cron_job(
                        mid: str = manifest.app_id,
                        factory: Callable[[], Any] = ctx_factory,
                    ) -> None:
                        ctx = await factory()
                        await safe_execute(mid, ctx, event_bus)

                    scheduler.add_job(cron_job, "cron", **_parse_cron(trigger.schedule))

                elif trigger.type == "hook":
                    _register_hook(app, manifest.app_id, ctx_factory, event_bus)

                elif trigger.type == "observer":
                    logger.warning(
                        "Observer trigger for %s: watchdog not yet wired",
                        manifest.app_id,
                    )

            if manifest.mcp_enabled:
                logger.info("MCP tool pending for %s (Task 13)", manifest.app_id)

            logger.info("Bootstrapped: %s", manifest.app_id)

        scheduler.start()


def _parse_cron(schedule: str) -> Dict[str, str]:
    minute, hour, day, month, day_of_week = schedule.split()
    return dict(minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week)


def _register_hook(
    app: FastAPI,
    app_id: str,
    ctx_factory: Callable[[], Any],
    event_bus: EventBus,
) -> None:
    async def handler() -> Dict[str, str]:
        ctx = await ctx_factory()
        await safe_execute(app_id, ctx, event_bus)
        return {"status": "ok", "app_id": app_id}

    app.add_api_route(f"/{app_id}/run", handler, methods=["POST"])

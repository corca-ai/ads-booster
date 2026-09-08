"""Explicit image-edit enrollment; no image generation during service setup."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from ads_booster.contracts.tool_capability import ToolReadiness
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.creative_image_edit import (
    CreativeImageEditTool,
    ImageEditConfig,
    image_edit_descriptor,
)
from ads_booster.marketing.agent_service.maintenance import MaintenanceGate
from ads_booster.providers.codex_image_edit import CodexImageEditProvider

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from threading import Event

    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.marketing.agent_service.application import MarketingAgentService

_MAX_CONFIG = 16000
_LOGGER = logging.getLogger(__name__)
_CACHE_SECONDS = 60
_CAPABILITIES = ("creative.image.edit", "creative.image.localize")


@dataclass
class ImageEditCatalog:
    base: ToolRegistry
    tool: CreativeImageEditTool
    _cached: ToolReadiness | None = field(default=None, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        with self._lock:
            if (
                self._cached is None
                or not 0 <= (now - self._cached.observed_at).total_seconds() < _CACHE_SECONDS
            ):
                try:
                    ready = self.tool.readiness()
                except Exception:  # noqa: BLE001 - sanitized readiness observation.
                    ready = False
                self._cached = ToolReadiness(
                    ready=ready,
                    observed_at=now,
                    max_age_seconds=_CACHE_SECONDS,
                    reason_code=None if ready else "image_edit_unavailable",
                )
            readiness = self._cached
        existing = tuple(
            d
            for d in self.base.current_descriptors(now=now)
            if d.capability_id not in _CAPABILITIES
        )
        return (
            *existing,
            *(
                image_edit_descriptor(
                    config=self.tool.config,
                    capability_id=capability,
                    now=readiness.observed_at,
                    ready=readiness.ready,
                ).model_copy(update={"readiness": readiness})
                for capability in _CAPABILITIES
            ),
        )


def connect_image_edit(
    service: MarketingAgentService,
    *,
    config_path: Path,
    now: datetime,
    on_completed: Callable[[str, str, str], None] | None = None,
) -> CreativeImageEditTool:
    if any(capability in service.tools for capability in _CAPABILITIES):
        raise ValueError("image_edit_tool_already_configured")
    with config_path.open("rb") as file:
        raw = file.read(_MAX_CONFIG + 1)
    if len(raw) > _MAX_CONFIG:
        raise ValueError("image_edit_config_too_large")
    config = ImageEditConfig.model_validate_json(raw)
    executable = Path(config.executable)
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("image_edit_executable_invalid")
    provider = CodexImageEditProvider(executable=executable, model=config.model_id)
    assets = SqliteCreativeAssetRepository(
        service.repository.database_path, service.repository.database_path.parent / "artifacts"
    )
    tool = CreativeImageEditTool(
        service=service,
        assets=assets,
        root=assets.artifact_root / "image-edits",
        provider=provider,
        config=config,
        readiness=lambda: provider.readiness().ready,
        on_completed=on_completed,
    )
    catalog = ImageEditCatalog(service.registry, tool)
    service.registry = ToolRegistry(catalog.descriptors(now=now), provider=catalog)
    service.tools = {**service.tools, **dict.fromkeys(_CAPABILITIES, tool)}
    return tool


def run_image_edit_worker(tool: CreativeImageEditTool, stop: Event, gate: MaintenanceGate) -> None:
    """Poll the durable queue inside the existing maintenance and shutdown boundary."""
    while not stop.is_set():
        try:
            with gate.work() as admitted:
                if admitted:
                    _ = tool.work_once()
        except Exception:  # noqa: BLE001 - queue owner retains uncertainty; no provider-error leakage.
            _LOGGER.warning("image_edit_queue_poll_failed")
        _ = stop.wait(1)

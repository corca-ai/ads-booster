"""Install the bundled Trace post tool and its durable worker."""
# ruff: noqa: EM101, PLR0913, TC001

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING

from ads_booster.agent.core.registry import ToolRegistration, ToolRegistry
from ads_booster.agent.service.maintenance import MaintenanceGate
from ads_booster.agent.service.trace_post import (
    CAPABILITY,
    TracePostConfig,
    TracePostTool,
    trace_post_descriptor,
)
from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
from ads_booster.providers.codex_trace_post import CodexTracePostProvider

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from threading import Event

    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.contracts.tool_capability import ToolDescriptor


@dataclass(frozen=True, slots=True)
class TracePostCatalog:
    tool: TracePostTool

    def registrations(self) -> tuple[ToolRegistration, ...]:
        return (
            ToolRegistration(
                capability_id=CAPABILITY,
                version="1",
                adapter=self.tool,
                descriptor_factory=self.descriptor,
            ),
        )

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        return ToolRegistry.from_registrations(self.registrations(), now=now).descriptors

    def descriptor(self, *, now: datetime) -> ToolDescriptor:
        ready = self.tool.config.executable.is_file() and self.tool.bundle.is_dir()
        return trace_post_descriptor(now=now, ready=ready)


def connect_trace_post(
    service: MarketingAgentService,
    *,
    executable: Path,
    model: str,
    timeout_seconds: float,
    now: datetime,
    on_completed: Callable[[str, str, str], None] | None = None,
) -> TracePostTool:
    if CAPABILITY in service.tools:
        raise ValueError("trace_post_tool_already_configured")
    _ = timeout_seconds
    config = TracePostConfig(executable.resolve(), model, 3600.0)
    root = service.repository.database_path.parent / "artifacts"
    bundle = Path(str(files("ads_booster").joinpath("trace_post_bundle")))
    tool = TracePostTool(
        service=service,
        assets=SqliteCreativeAssetRepository(service.repository.database_path, root),
        root=root / "trace-post",
        bundle=bundle,
        provider=CodexTracePostProvider(config.executable, config.model),
        config=config,
        on_completed=on_completed,
    )
    service.install_tool_catalog(TracePostCatalog(tool), now=now)
    return tool


def run_trace_post_worker(tool: TracePostTool, stop: Event, gate: MaintenanceGate) -> None:
    while not stop.is_set():
        try:
            with gate.work() as admitted:
                if admitted:
                    _ = tool.work_once()
        except Exception:  # noqa: BLE001 - durable queue state survives worker failures.
            _LOGGER.warning("trace_post_queue_poll_failed")
        _ = stop.wait(1)


__all__ = ["TracePostCatalog", "connect_trace_post", "run_trace_post_worker"]

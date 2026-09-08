"""Explicit local Mac capture composition; server onboarding remains independent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from ads_booster.capture.appium_codex import CodexAppiumJobAdapter
from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.calendar_preparation import SimctlEventKitCalendarDataPort
from ads_booster.capture.readiness import DefaultCaptureReadiness
from ads_booster.capture.simulator_photo import SimctlPhotoImporter
from ads_booster.capture.wallpaper_collection import SimctlAppGroupWallpaperCollector
from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.contracts.models import ContractModel, DeviceTarget
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.capture_readiness import CaptureReadinessProbe
from ads_booster.marketing.agent_service.creative_assets import SqliteCreativeAssetRepository
from ads_booster.marketing.agent_service.creative_capture import (
    CreativeCaptureTool,
    creative_capture_descriptor,
)
from ads_booster.marketing.tool_adapters.compatibility import appium_adapter

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from ads_booster.contracts.agent_run import AgentRun
    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.marketing.agent_service.application import MarketingAgentService
    from ads_booster.providers.codex_cli import CodexCli

_MAX_CONFIG_BYTES = 16000


def _shared_capture_scope(run: AgentRun) -> CreativeScope:
    # Slack's trusted DM admission assigns this isolated tenant namespace.
    # The private service also excludes capture through its capability policy.
    if run.tenant_id.startswith("slack-private-"):
        raise ValueError("capture_private_run_not_enabled")
    return CreativeScope(workspace_id=run.tenant_id, product_id="trace")


class LocalCaptureConfig(ContractModel):
    device: DeviceTarget
    appium_server: str = "http://127.0.0.1:4723"
    timeout_seconds: Annotated[float, Field(ge=30, le=3600)] = 300


@dataclass(frozen=True, slots=True)
class CaptureCatalog:
    base: ToolRegistry
    probe: CaptureReadinessProbe

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        readiness = self.probe.check(now=now)
        descriptor = creative_capture_descriptor(
            now=now, ready=readiness.ready, reason_code=readiness.reason_code
        ).model_copy(update={"readiness": readiness})
        return (*self.base.current_descriptors(now=now), descriptor)


def connect_local_capture(
    service: MarketingAgentService, *, config_path: Path, codex: CodexCli, now: datetime
) -> None:
    with config_path.open("rb") as stream:
        data = stream.read(_MAX_CONFIG_BYTES + 1)
    if len(data) > _MAX_CONFIG_BYTES:
        raise ValueError("capture_configuration_too_large")
    config = LocalCaptureConfig.model_validate_json(data)
    _ = validate_appium_server_url(config.appium_server)
    if "capture.appium" in service.tools:
        raise ValueError("capture_tool_already_registered")
    root = service.repository.database_path.parent
    worker = CodexAppiumJobAdapter(
        codex=codex,
        simulator=SimctlPhotoImporter(),
        collector=SimctlAppGroupWallpaperCollector(),
        calendar=SimctlEventKitCalendarDataPort(),
        readiness=DefaultCaptureReadiness(appium_server=config.appium_server),
    )
    tool = CreativeCaptureTool(
        repository=service.repository,
        assets=SqliteCreativeAssetRepository(service.repository.database_path, root / "artifacts"),
        job_root=root / "artifacts" / "capture-jobs",
        worker=worker,
        device=config.device,
        appium_server=config.appium_server,
        scope_for_run=_shared_capture_scope,
        timeout_seconds=config.timeout_seconds,
    )
    catalog = CaptureCatalog(
        service.registry,
        CaptureReadinessProbe(config.device, config.appium_server, codex.executable),
    )
    descriptors = catalog.descriptors(now=now)
    service.tools = {
        **service.tools,
        "capture.appium": appium_adapter(
            executor_id="local-mac-codex-appium", executor=tool.execute
        ),
    }
    service.registry = ToolRegistry(descriptors, provider=catalog)

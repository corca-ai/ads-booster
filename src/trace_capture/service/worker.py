from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from anyio import sleep
from anyio.to_thread import run_sync

from trace_capture.automation import (
    AutomationQueue,
    CampaignProducer,
    CampaignStore,
    GenerateOnePort,
    GenerateOneWorker,
    QueueRecord,
    QueueScheduler,
)
from trace_capture.capture.capture_safety import CaptureAdapterError
from trace_capture.capture.factory import build_capture_adapter
from trace_capture.capture.readiness import DefaultCaptureReadiness
from trace_capture.contracts.results import TraceRunResult
from trace_capture.contracts.run import TraceRunState
from trace_capture.default_assets import default_iphone_ui_path
from trace_capture.runtime.generate_one import (
    BackgroundFetcher,
    GenerateOneError,
    GenerateOneOptions,
    GenerateOneRunner,
)
from trace_capture.search.image.background import (
    BackgroundSearchError,
    ImageSearchBackgroundFetcher,
)
from trace_capture.search.image.providers import create_image_search_provider

if TYPE_CHECKING:
    from collections.abc import Callable

    from trace_capture.contracts.generation import MarketingContextBundle
    from trace_capture.transport.http import HttpClient

_DEFAULT_POLL_INTERVAL_SECONDS: Final = 0.25
_DEFAULT_LEASE_SECONDS: Final = 300.0
_DEFAULT_APPIUM_SERVER: Final = "http://127.0.0.1:4723"
_DEFAULT_IPHONE_UI: Final = default_iphone_ui_path()


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class AutomationServiceWorker:
    producer: CampaignProducer
    scheduler: QueueScheduler
    worker: GenerateOneWorker
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS
    clock: Callable[[], datetime] = _utc_now
    on_completed: Callable[[QueueRecord], None] | None = None

    async def run(self) -> None:
        while True:
            now = self.clock()
            _ = self.producer.tick(now)
            claimed = self.scheduler.poll(now)
            if claimed is None:
                await sleep(self.poll_interval_seconds)
                continue
            run_claim = partial(self.worker.run_claim, claimed, now=now)
            completed = await run_sync(run_claim)
            if self.on_completed is not None:
                self.on_completed(completed)


@dataclass(frozen=True, slots=True)
class ServiceWorkerConfig:
    worker_id: str | None = None
    lease_seconds: float = _DEFAULT_LEASE_SECONDS
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS
    on_completed: Callable[[QueueRecord], None] | None = None


@dataclass(frozen=True, slots=True)
class ProductionGenerateOneRunner:
    options: GenerateOneOptions
    background_fetcher: BackgroundFetcher

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        try:
            adapter = build_capture_adapter(
                device_kind=bundle.device.kind,
                appium_server=self.options.appium_server,
                readiness=self.options.capture_readiness,
            )
            return GenerateOneRunner(
                options=self.options,
                background_fetcher=self.background_fetcher,
                capture_adapter=adapter,
            ).run(bundle)
        except (
            BackgroundSearchError,
            CaptureAdapterError,
            GenerateOneError,
            OSError,
        ):
            return TraceRunResult(
                run_id=bundle.request_id,
                idempotency_key=f"{bundle.request_id}-v1",
                input_digest=sha256(bundle.model_dump_json().encode()).hexdigest(),
                state=TraceRunState.FAILED,
            )


def create_service_worker(
    home: Path,
    runner: GenerateOnePort,
    *,
    config: ServiceWorkerConfig | None = None,
) -> AutomationServiceWorker:
    selected_config = ServiceWorkerConfig() if config is None else config
    queue = AutomationQueue(home)
    selected_worker_id = selected_config.worker_id or f"trace-agent-{os.getpid()}-{uuid4().hex}"
    return AutomationServiceWorker(
        producer=CampaignProducer(CampaignStore(home), queue),
        scheduler=QueueScheduler(
            queue=queue,
            worker_id=selected_worker_id,
            lease_seconds=selected_config.lease_seconds,
        ),
        worker=GenerateOneWorker(
            queue=queue,
            runner=runner,
            artifact_root=home / "generated",
        ),
        poll_interval_seconds=selected_config.poll_interval_seconds,
        on_completed=selected_config.on_completed,
    )


def build_production_runner(home: Path, http: HttpClient) -> GenerateOnePort:
    appium_server = os.environ.get("TRACE_AGENT_APPIUM_SERVER", _DEFAULT_APPIUM_SERVER)
    iphone_ui = Path(os.environ.get("TRACE_AGENT_IPHONE_UI", str(_DEFAULT_IPHONE_UI)))
    readiness = DefaultCaptureReadiness(appium_server=appium_server)
    options = GenerateOneOptions(
        output_root=home / "generated",
        state_root=home / "state",
        capture_output_root=home / "capture",
        iphone_ui_path=iphone_ui.expanduser().resolve(),
        appium_server=appium_server,
        timeout_seconds=float(os.environ.get("TRACE_AGENT_GENERATION_TIMEOUT_SECONDS", "120")),
        capture_readiness=readiness,
    )
    return ProductionGenerateOneRunner(
        options=options,
        background_fetcher=ImageSearchBackgroundFetcher(
            image_search=create_image_search_provider(
                http=http,
                provider_name=os.environ.get("TRACE_AGENT_WEB_SEARCH_PROVIDER", "auto"),
                timeout_seconds=float(
                    os.environ.get("TRACE_AGENT_WEB_SEARCH_TIMEOUT_SECONDS", "30")
                ),
            ),
            http=http,
        ),
    )

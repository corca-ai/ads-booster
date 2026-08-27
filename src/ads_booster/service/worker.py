from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from anyio import sleep
from anyio.to_thread import run_sync

from ads_booster.automation import (
    AutomationQueue,
    CampaignProducer,
    CampaignStore,
    GenerateOnePort,
    GenerateOneWorker,
    QueueRecord,
    QueueScheduler,
)
from ads_booster.connectors.trace.v1.codex_runtime import build_codex_trace_runner

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.transport.http import HttpClient

_DEFAULT_POLL_INTERVAL_SECONDS: Final = 0.25
_DEFAULT_LEASE_SECONDS: Final = 300.0


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


def create_service_worker(
    home: Path,
    runner: GenerateOnePort,
    *,
    config: ServiceWorkerConfig | None = None,
) -> AutomationServiceWorker:
    selected_config = ServiceWorkerConfig() if config is None else config
    queue = AutomationQueue(home)
    selected_worker_id = selected_config.worker_id or f"trace-marketing-{os.getpid()}-{uuid4().hex}"
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


def build_production_runner(
    home: Path,
    http: HttpClient,
    *,
    before_side_effect: Callable[[str], None] | None = None,
) -> GenerateOnePort:
    return build_codex_trace_runner(home, http, before_side_effect=before_side_effect)

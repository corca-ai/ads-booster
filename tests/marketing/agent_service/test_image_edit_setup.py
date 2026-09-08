"""Planning readiness is cached honestly while execution probes remain fresh."""

from __future__ import annotations

from datetime import timedelta
from threading import Event
from typing import TYPE_CHECKING, override

from ads_booster.bootstrap.image_edit_setup import (
    ImageEditCatalog,
    run_image_edit_worker,
)
from ads_booster.agent.service.maintenance import MaintenanceGate
from tests.marketing.agent_service.test_creative_image_edit import setup
from tests.marketing.channels.test_slack_commands import NOW

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


def test_planning_cache_preserves_observation_time_and_execution_probe(tmp_path: Path) -> None:
    tool, _ = setup(tmp_path)
    calls: list[bool] = []

    def probe() -> bool:
        calls.append(True)
        return True

    tool.readiness = probe
    catalog = ImageEditCatalog(tool)
    first = catalog.descriptors(now=NOW)
    cached = catalog.descriptors(now=NOW + timedelta(seconds=59))
    assert len(calls) == 1
    assert cached[0].readiness.observed_at == first[0].readiness.observed_at
    assert cached[0].readiness.max_age_seconds == 60
    _ = catalog.descriptors(now=NOW + timedelta(seconds=60))
    assert len(calls) == 2
    assert tool.readiness()
    assert len(calls) == 3


def test_background_poll_respects_maintenance_and_shared_stop(tmp_path: Path) -> None:
    tool, _ = setup(tmp_path)
    calls: list[bool] = []
    marker = tmp_path / "maintenance"
    marker.touch()
    stop = Event()
    gate = MaintenanceGate(marker)

    def work() -> JsonObject:
        calls.append(True)
        assert gate.active == 1
        stop.set()
        return {"state": "idle"}

    tool.work_once = work
    stop.set()
    run_image_edit_worker(tool, stop, gate)
    assert calls == []
    stop.clear()

    # A stopped maintenance loop never enters the worker.
    class StopAfterWait(Event):
        @override
        def wait(self, timeout: float | None = None) -> bool:
            _ = timeout
            self.set()
            return True

    run_image_edit_worker(tool, StopAfterWait(), gate)
    assert calls == []
    marker.unlink()
    run_image_edit_worker(tool, stop, gate)
    assert calls == [True]
    assert gate.active == 0

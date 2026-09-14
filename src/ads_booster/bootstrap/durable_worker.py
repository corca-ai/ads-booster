"""Shared maintenance and shutdown boundary for durable tool workers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from ads_booster.agent.service.maintenance import MaintenanceGate
    from ads_booster.transport.json_types import JsonObject


def run_durable_worker(
    work_once: Callable[[], JsonObject],
    stop: Event,
    gate: MaintenanceGate,
    on_failure: Callable[[], None],
) -> None:
    while not stop.is_set():
        try:
            with gate.work() as admitted:
                if admitted:
                    _ = work_once()
        except Exception:  # noqa: BLE001 - durable owner retains uncertainty across polls.
            on_failure()
        _ = stop.wait(1)

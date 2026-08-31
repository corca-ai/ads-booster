from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl

if TYPE_CHECKING:
    from collections.abc import Generator

    from ads_booster.capture.calendar_automation_contract import CalendarPreparation
    from ads_booster.capture.calendar_preparation import CalendarDataPort
    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract

_CLEANUP_TIMEOUT_SECONDS: Final = 30.0


@contextmanager
def prepared_calendar(
    port: CalendarDataPort,
    contract: CodexAppiumJobContract,
    control: CaptureControl,
) -> Generator[CalendarPreparation]:
    preparation = port.prepare(contract, control)
    try:
        yield preparation
    finally:
        primary_error = sys.exception()
        try:
            port.cleanup(contract, preparation, _cleanup_control(control))
        except CaptureAdapterError as cleanup_error:
            if isinstance(primary_error, CaptureAdapterError):
                raise primary_error.with_cleanup_error(str(cleanup_error)) from cleanup_error
            if primary_error is not None:
                raise primary_error from cleanup_error
            raise


def _cleanup_control(control: CaptureControl) -> CaptureControl:
    return CaptureControl(
        expires_at=control.clock.monotonic() + _CLEANUP_TIMEOUT_SECONDS,
        cancel_file=None,
        clock=control.clock,
        sleeper=control.sleeper,
    )


__all__ = ["prepared_calendar"]

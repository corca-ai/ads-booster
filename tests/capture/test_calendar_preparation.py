from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

import pytest

from ads_booster.capture.calendar_preparation import (
    CalendarAutomationOperation,
    CalendarAutomationRequest,
    CalendarAutomationResult,
    CalendarPreparation,
    SimctlEventKitCalendarDataPort,
    build_calendar_events,
)
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureClock,
    CaptureControl,
    CaptureSleeper,
)
from ads_booster.capture.simctl_command import CommandResult
from ads_booster.contracts import ErrorCode

from .codex_appium_support import V2JobInputs, v2_contract

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract


UDID = "E1FB798D-79E6-4B25-A987-D298A4FD122A"
REQUEST_FILENAME = "trace_marketing_calendar_request.json"
RESULT_FILENAME = "trace_marketing_calendar_result.json"


@dataclass(frozen=True, slots=True)
class FixedClock:
    now: float = 100.0

    def monotonic(self) -> float:
        return self.now

    def time_ns(self) -> int:
        return int(self.now * 1_000_000_000)


DEFAULT_CLOCK: Final = FixedClock()


@dataclass(frozen=True, slots=True)
class ResultWritingSleeper:
    result_path: Path
    result_payload: str
    sleeps: list[float] = field(default_factory=list)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if len(self.sleeps) == 1:
            _ = self.result_path.write_text(self.result_payload, encoding="utf-8")


@dataclass(frozen=True, slots=True)
class CalendarAutomationRunner:
    container: Path
    request_path: Path
    result_path: Path
    response_payloads: list[str] = field(default_factory=list)
    commands: list[tuple[str, ...]] = field(default_factory=list)
    requests: list[CalendarAutomationRequest] = field(default_factory=list)

    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        del timeout_seconds
        self.commands.append(command)
        if command == (
            "xcrun",
            "simctl",
            "privacy",
            UDID,
            "grant",
            "calendar",
            "com.corca.Trace",
        ):
            return CommandResult(stdout="", returncode=0)
        if command == (
            "xcrun",
            "simctl",
            "get_app_container",
            UDID,
            "com.corca.Trace",
            "groups",
        ):
            return CommandResult(
                stdout=f"group.ai.corca.trace\t{self.container}\n",
                returncode=0,
            )
        if command[:3] == ("xcrun", "simctl", "launch"):
            assert not self.result_path.exists()
            request = CalendarAutomationRequest.model_validate_json(
                self.request_path.read_text(encoding="utf-8")
            )
            self.requests.append(request)
            if self.response_payloads:
                _ = self.result_path.write_text(
                    self.response_payloads.pop(0),
                    encoding="utf-8",
                )
            return CommandResult(stdout="", returncode=0)
        msg = f"unexpected command: {command}"
        raise AssertionError(msg)


def control_with(
    sleeper: CaptureSleeper,
    clock: CaptureClock = DEFAULT_CLOCK,
) -> CaptureControl:
    return CaptureControl(
        expires_at=clock.monotonic() + 5,
        cancel_file=None,
        clock=clock,
        sleeper=sleeper,
    )


def prepare_result(contract: CodexAppiumJobContract) -> CalendarAutomationResult:
    return CalendarAutomationResult(
        schema_version="trace.marketing-calendar-automation-result.v1",
        operation=CalendarAutomationOperation.PREPARE,
        request_sha256=contract.request_sha256,
        calendar_namespace=contract.calendar_namespace,
        status="completed",
        calendar_identifier="trace-calendar-identifier",
        event_count=2,
    )


def test_calendar_result_when_swift_omits_nil_identifier_then_parses_failure() -> None:
    # Given Swift JSONEncoder omitted an optional nil calendar identifier
    payload = json.dumps(
        {
            "schema_version": "trace.marketing-calendar-automation-result.v1",
            "operation": "prepare",
            "request_sha256": "a" * 64,
            "calendar_namespace": "trace-request-1",
            "status": "failed",
            "event_count": 0,
            "error_code": "calendar_verification_failed",
        },
        separators=(",", ":"),
    )

    # When the Python worker parses the helper failure contract
    result = CalendarAutomationResult.model_validate_json(payload)

    # Then the omitted optional identifier remains None without hiding the failure
    assert result.calendar_identifier is None
    assert result.error_code == "calendar_verification_failed"


def test_build_calendar_events_when_v2_items_include_timed_and_all_day_then_uses_local_day() -> (
    None
):
    # Given a Korean v2 job whose input includes one timed item and one all-day item
    contract = v2_contract(V2JobInputs(trace_items=("09:30 집중 작업", "독서의 날")))
    local_zone = ZoneInfo(contract.time_zone)

    # When worker preparation converts the immutable context into EventKit event inputs
    events = build_calendar_events(contract)

    # Then dates and times bind deterministically to the reference day in the configured zone
    assert tuple(
        (event.title, event.starts_at_epoch, event.ends_at_epoch, event.is_all_day)
        for event in events
    ) == (
        (
            "집중 작업",
            int(datetime(2026, 8, 28, 9, 30, tzinfo=local_zone).timestamp()),
            int(datetime(2026, 8, 28, 10, 30, tzinfo=local_zone).timestamp()),
            False,
        ),
        (
            "독서의 날",
            int(datetime(2026, 8, 28, tzinfo=local_zone).timestamp()),
            int(datetime(2026, 8, 29, tzinfo=local_zone).timestamp()),
            True,
        ),
    )


def test_prepare_when_stale_result_exists_then_clears_polls_and_returns_typed_preparation(
    tmp_path: Path,
) -> None:
    # Given a request App Group containing a stale result before this job starts
    contract = v2_contract(V2JobInputs(trace_items=("09:30 집중 작업", "독서의 날")))
    container = tmp_path / "app-group"
    container.mkdir()
    request_path = container / REQUEST_FILENAME
    result_path = container / RESULT_FILENAME
    _ = result_path.write_text('{"stale":true}', encoding="utf-8")
    result_payload = prepare_result(contract).model_dump_json()
    sleeper = ResultWritingSleeper(result_path, result_payload)
    runner = CalendarAutomationRunner(container, request_path, result_path)
    port = SimctlEventKitCalendarDataPort(
        runner=runner,
        poll_interval_seconds=0.01,
    )

    # When the worker prepares the request-owned Calendar data through Trace's helper mode
    preparation = port.prepare(contract, control_with(sleeper))

    # Then the request binds its namespace and digest, polls a fresh response, and returns its proof
    assert preparation == CalendarPreparation(
        request_sha256=contract.request_sha256,
        calendar_namespace=contract.calendar_namespace,
        calendar_identifier="trace-calendar-identifier",
        event_count=2,
    )
    assert runner.requests == [
        CalendarAutomationRequest(
            schema_version="trace.marketing-calendar-automation.v1",
            operation=CalendarAutomationOperation.PREPARE,
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.calendar_namespace,
            calendar_identifier=None,
            events=build_calendar_events(contract),
        )
    ]
    assert runner.commands[-1] == (
        "xcrun",
        "simctl",
        "launch",
        "--terminate-running-process",
        contract.device.udid,
        contract.bundle_id,
        *contract.launch_arguments,
        "-traceMarketingCalendarAutomation",
    )
    assert runner.commands[0] == (
        "xcrun",
        "simctl",
        "privacy",
        contract.device.udid,
        "grant",
        "calendar",
        contract.bundle_id,
    )
    assert contract.launch_arguments[-1] == contract.device.udid
    assert sleeper.sleeps == [0.01]


def test_prepare_when_trace_reports_failure_then_raises_typed_capture_error(
    tmp_path: Path,
) -> None:
    # Given Trace's EventKit helper reports an explicit preparation failure for this request
    contract = v2_contract()
    container = tmp_path / "app-group"
    container.mkdir()
    request_path = container / REQUEST_FILENAME
    result_path = container / RESULT_FILENAME
    failure = CalendarAutomationResult(
        schema_version="trace.marketing-calendar-automation-result.v1",
        operation=CalendarAutomationOperation.PREPARE,
        request_sha256=contract.request_sha256,
        calendar_namespace=contract.calendar_namespace,
        status="failed",
        calendar_identifier=None,
        event_count=0,
        error_code="calendar_access_denied",
    )
    runner = CalendarAutomationRunner(
        container,
        request_path,
        result_path,
        [failure.model_dump_json()],
    )
    port = SimctlEventKitCalendarDataPort(runner=runner)

    # When worker preparation consumes the helper's result contract
    with pytest.raises(CaptureAdapterError) as raised:
        _ = port.prepare(contract, control_with(ResultWritingSleeper(result_path, "")))

    # Then an explicit native failure cannot be mistaken for ready Calendar data
    assert raised.value.code is ErrorCode.CALENDAR_PREPARATION_FAILED


def test_cleanup_when_preparation_is_request_owned_then_removes_only_that_calendar(
    tmp_path: Path,
) -> None:
    # Given successful preparation evidence for one immutable v2 request
    contract = v2_contract()
    preparation = CalendarPreparation(
        request_sha256=contract.request_sha256,
        calendar_namespace=contract.calendar_namespace,
        calendar_identifier="trace-calendar-identifier",
        event_count=1,
    )
    container = tmp_path / "app-group"
    container.mkdir()
    request_path = container / REQUEST_FILENAME
    result_path = container / RESULT_FILENAME
    cleanup = CalendarAutomationResult(
        schema_version="trace.marketing-calendar-automation-result.v1",
        operation=CalendarAutomationOperation.CLEANUP,
        request_sha256=contract.request_sha256,
        calendar_namespace=contract.calendar_namespace,
        status="completed",
        calendar_identifier="trace-calendar-identifier",
        event_count=0,
    )
    runner = CalendarAutomationRunner(
        container,
        request_path,
        result_path,
        [cleanup.model_dump_json()],
    )
    port = SimctlEventKitCalendarDataPort(runner=runner)

    # When the worker asks Trace to clean up that completed preparation
    port.cleanup(
        contract,
        preparation,
        control_with(ResultWritingSleeper(result_path, "")),
    )

    # Then cleanup uses the same request binding and targets no other Calendar data
    assert runner.requests == [
        CalendarAutomationRequest(
            schema_version="trace.marketing-calendar-automation.v1",
            operation=CalendarAutomationOperation.CLEANUP,
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.calendar_namespace,
            calendar_identifier="trace-calendar-identifier",
            events=build_calendar_events(contract),
        )
    ]

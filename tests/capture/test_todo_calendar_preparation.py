from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from ads_booster.capture.calendar_preparation import (
    CalendarAutomationOperation,
    CalendarAutomationResult,
    CalendarPreparation,
    SimctlEventKitCalendarDataPort,
    build_todo_calendar_events,
)
from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.capture.codex_appium_job import CodexAppiumJobContract

from .codex_appium_contract_support import V2JobInputs, v2_contract
from .test_calendar_preparation import (
    REQUEST_FILENAME,
    RESULT_FILENAME,
    CalendarAutomationRunner,
    ResultWritingSleeper,
    control_with,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_v2_job_contract_when_todo_calendar_is_request_owned_then_accepts_it() -> None:
    # Given a valid job payload with a separate request-owned calendar for capture todos
    payload = v2_contract().model_dump(mode="json")
    payload["todo_calendar_namespace"] = "trace-request-1-todos"
    del payload["request_sha256"]
    del payload["launch_arguments"]

    # When the worker binds the payload before invoking native automation
    contract = CodexAppiumJobContract.model_validate(payload)

    # Then the todo calendar is part of the immutable request contract
    assert contract.todo_calendar_namespace == "trace-request-1-todos"


def test_build_todo_calendar_events_when_request_has_todos_then_uses_capture_day() -> None:
    # Given a Korean capture request with two undated todos
    contract = v2_contract(V2JobInputs(trace_todos=("물감놀이 앞치마", "기차표 저장")))
    local_zone = ZoneInfo(contract.time_zone)

    # When the worker projects them into an isolated display calendar
    events = build_todo_calendar_events(contract)

    # Then each title is an all-day event on the capture day
    start = int(datetime(2026, 8, 28, tzinfo=local_zone).timestamp())
    end = int(datetime(2026, 8, 29, tzinfo=local_zone).timestamp())
    assert tuple(
        (event.title, event.starts_at_epoch, event.ends_at_epoch, event.is_all_day)
        for event in events
    ) == (
        ("물감놀이 앞치마", start, end, True),
        ("기차표 저장", start, end, True),
    )


def test_prepare_when_request_has_todos_then_creates_two_owned_calendars(
    tmp_path: Path,
) -> None:
    # Given a request with schedule rows and todos that must not use Trace's shared list
    contract = v2_contract(
        V2JobInputs(trace_items=("Focus block",), trace_todos=("Pay rent", "Book dentist"))
    )
    container = tmp_path / "app-group"
    container.mkdir()
    request_path = container / REQUEST_FILENAME
    result_path = container / RESULT_FILENAME
    responses = [
        CalendarAutomationResult(
            schema_version="trace.marketing-calendar-automation-result.v1",
            operation=CalendarAutomationOperation.PREPARE,
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.todo_calendar_namespace,
            status="completed",
            calendar_identifier="todo-calendar-id",
            event_count=2,
        ).model_dump_json(),
        CalendarAutomationResult(
            schema_version="trace.marketing-calendar-automation-result.v1",
            operation=CalendarAutomationOperation.PREPARE,
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.calendar_namespace,
            status="completed",
            calendar_identifier="schedule-calendar-id",
            event_count=1,
        ).model_dump_json(),
    ]
    runner = CalendarAutomationRunner(
        container,
        request_path,
        result_path,
        response_payloads=responses,
    )
    port = SimctlEventKitCalendarDataPort(runner=runner)

    # When the worker prepares native data before Codex opens the editor
    preparation = port.prepare(contract, control_with(ResultWritingSleeper(result_path, "")))

    # Then todo and schedule rows live separately, with schedule prepared last for editor startup
    assert preparation.todo_calendar_namespace == contract.todo_calendar_namespace
    assert preparation.todo_calendar_identifier == "todo-calendar-id"
    assert preparation.todo_event_count == 2
    assert [request.calendar_namespace for request in runner.requests] == [
        contract.todo_calendar_namespace,
        contract.calendar_namespace,
    ]
    assert runner.requests[0].events == build_todo_calendar_events(contract)


def test_cleanup_when_todo_calendar_exists_then_removes_both_owned_calendars(
    tmp_path: Path,
) -> None:
    # Given a completed preparation with separate schedule and todo calendars
    contract = v2_contract(V2JobInputs(trace_todos=("Pay rent", "Book dentist")))
    preparation = CalendarPreparation(
        request_sha256=contract.request_sha256,
        calendar_namespace=contract.calendar_namespace,
        calendar_identifier="schedule-calendar-id",
        event_count=1,
        todo_calendar_namespace=contract.todo_calendar_namespace,
        todo_calendar_identifier="todo-calendar-id",
        todo_event_count=2,
    )
    container = tmp_path / "app-group"
    container.mkdir()
    request_path = container / REQUEST_FILENAME
    result_path = container / RESULT_FILENAME
    responses = [
        CalendarAutomationResult(
            schema_version="trace.marketing-calendar-automation-result.v1",
            operation=CalendarAutomationOperation.CLEANUP,
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.todo_calendar_namespace,
            status="completed",
            calendar_identifier="todo-calendar-id",
            event_count=0,
        ).model_dump_json(),
        CalendarAutomationResult(
            schema_version="trace.marketing-calendar-automation-result.v1",
            operation=CalendarAutomationOperation.CLEANUP,
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.calendar_namespace,
            status="completed",
            calendar_identifier="schedule-calendar-id",
            event_count=0,
        ).model_dump_json(),
    ]
    runner = CalendarAutomationRunner(
        container,
        request_path,
        result_path,
        response_payloads=responses,
    )

    # When worker cleanup runs after collecting the wallpaper
    SimctlEventKitCalendarDataPort(runner=runner).cleanup(
        contract,
        preparation,
        control_with(ResultWritingSleeper(result_path, "")),
    )

    # Then only the two request-owned identifiers are removed, todos first
    assert [
        (request.calendar_namespace, request.calendar_identifier) for request in runner.requests
    ] == [
        (contract.todo_calendar_namespace, "todo-calendar-id"),
        (contract.calendar_namespace, "schedule-calendar-id"),
    ]


def test_prepare_when_schedule_calendar_fails_then_rolls_back_todo_calendar(
    tmp_path: Path,
) -> None:
    # Given the todo calendar succeeds but the final schedule preparation fails
    contract = v2_contract(V2JobInputs(trace_todos=("Pay rent",)))
    container = tmp_path / "app-group"
    container.mkdir()
    request_path = container / REQUEST_FILENAME
    result_path = container / RESULT_FILENAME
    responses = [
        CalendarAutomationResult(
            schema_version="trace.marketing-calendar-automation-result.v1",
            operation=CalendarAutomationOperation.PREPARE,
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.todo_calendar_namespace,
            status="completed",
            calendar_identifier="todo-calendar-id",
            event_count=1,
        ).model_dump_json(),
        CalendarAutomationResult(
            schema_version="trace.marketing-calendar-automation-result.v1",
            operation=CalendarAutomationOperation.PREPARE,
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.calendar_namespace,
            status="failed",
            calendar_identifier=None,
            event_count=0,
            error_code="calendar_verification_failed",
        ).model_dump_json(),
        CalendarAutomationResult(
            schema_version="trace.marketing-calendar-automation-result.v1",
            operation=CalendarAutomationOperation.CLEANUP,
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.todo_calendar_namespace,
            status="completed",
            calendar_identifier="todo-calendar-id",
            event_count=0,
        ).model_dump_json(),
    ]
    runner = CalendarAutomationRunner(
        container,
        request_path,
        result_path,
        response_payloads=responses,
    )

    # When the worker cannot complete both request-owned resources
    with pytest.raises(CaptureAdapterError, match="calendar_verification_failed"):
        _ = SimctlEventKitCalendarDataPort(runner=runner).prepare(
            contract,
            control_with(ResultWritingSleeper(result_path, "")),
        )

    # Then it removes the already-created todo calendar before returning the failure
    assert [(request.operation, request.calendar_namespace) for request in runner.requests] == [
        (CalendarAutomationOperation.PREPARE, contract.todo_calendar_namespace),
        (CalendarAutomationOperation.PREPARE, contract.calendar_namespace),
        (CalendarAutomationOperation.CLEANUP, contract.todo_calendar_namespace),
    ]

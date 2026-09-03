from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol

from ads_booster.capture.calendar_automation_client import SimctlCalendarAutomationClient
from ads_booster.capture.calendar_automation_contract import (
    CalendarAutomationEvent,
    CalendarAutomationOperation,
    CalendarAutomationRequest,
    CalendarAutomationResult,
    CalendarPreparation,
    build_calendar_events,
    build_todo_calendar_events,
)
from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.capture.simctl_command import CommandRunner, SubprocessCommandRunner
from ads_booster.contracts import ErrorCode

if TYPE_CHECKING:
    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract

_REQUEST_SCHEMA: Final = "trace.marketing-calendar-automation.v1"
_CLEANUP_TIMEOUT_SECONDS: Final = 30.0


class CalendarDataPort(Protocol):
    def prepare(
        self,
        contract: CodexAppiumJobContract,
        control: CaptureControl,
    ) -> CalendarPreparation: ...

    def cleanup(
        self,
        contract: CodexAppiumJobContract,
        preparation: CalendarPreparation,
        control: CaptureControl,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SimctlEventKitCalendarDataPort:
    xcrun: str = "xcrun"
    runner: CommandRunner = field(default_factory=SubprocessCommandRunner)
    poll_interval_seconds: float = 0.05

    def prepare(
        self,
        contract: CodexAppiumJobContract,
        control: CaptureControl,
    ) -> CalendarPreparation:
        schedule_events = build_calendar_events(contract)
        todo_events = build_todo_calendar_events(contract)
        if not todo_events:
            schedule_identifier = self._prepare_calendar(
                contract,
                contract.calendar_namespace,
                schedule_events,
                control,
            )
            return CalendarPreparation(
                request_sha256=contract.request_sha256,
                calendar_namespace=contract.calendar_namespace,
                calendar_identifier=schedule_identifier,
                event_count=len(schedule_events),
            )
        todo_identifier = self._prepare_calendar(
            contract,
            contract.todo_calendar_namespace,
            todo_events,
            control,
        )
        try:
            schedule_identifier = self._prepare_calendar(
                contract,
                contract.calendar_namespace,
                schedule_events,
                control,
            )
        except CaptureAdapterError as error:
            try:
                self._cleanup_calendar(
                    contract,
                    contract.todo_calendar_namespace,
                    todo_identifier,
                    todo_events,
                    _cleanup_control(control),
                )
            except CaptureAdapterError as cleanup_error:
                raise error.with_cleanup_error(str(cleanup_error)) from cleanup_error
            raise
        return CalendarPreparation(
            request_sha256=contract.request_sha256,
            calendar_namespace=contract.calendar_namespace,
            calendar_identifier=schedule_identifier,
            event_count=len(schedule_events),
            todo_calendar_namespace=contract.todo_calendar_namespace,
            todo_calendar_identifier=todo_identifier,
            todo_event_count=len(todo_events),
        )

    def cleanup(
        self,
        contract: CodexAppiumJobContract,
        preparation: CalendarPreparation,
        control: CaptureControl,
    ) -> None:
        if (
            preparation.request_sha256 != contract.request_sha256
            or preparation.calendar_namespace != contract.calendar_namespace
            or preparation.todo_calendar_namespace not in {None, contract.todo_calendar_namespace}
        ):
            raise self._failure(
                CalendarAutomationOperation.CLEANUP,
                "calendar cleanup binding does not match the request",
            )
        cleanup_error: CaptureAdapterError | None = None
        todo_events = build_todo_calendar_events(contract)
        if preparation.todo_calendar_identifier is not None:
            try:
                self._cleanup_calendar(
                    contract,
                    contract.todo_calendar_namespace,
                    preparation.todo_calendar_identifier,
                    todo_events,
                    control,
                )
            except CaptureAdapterError as error:
                cleanup_error = error
        try:
            self._cleanup_calendar(
                contract,
                contract.calendar_namespace,
                preparation.calendar_identifier,
                build_calendar_events(contract),
                control,
            )
        except CaptureAdapterError as error:
            if cleanup_error is not None:
                raise cleanup_error.with_cleanup_error(str(error)) from error
            raise
        if cleanup_error is not None:
            raise cleanup_error

    def _prepare_calendar(
        self,
        contract: CodexAppiumJobContract,
        namespace: str,
        events: tuple[CalendarAutomationEvent, ...],
        control: CaptureControl,
    ) -> str:
        request = CalendarAutomationRequest(
            schema_version=_REQUEST_SCHEMA,
            operation=CalendarAutomationOperation.PREPARE,
            request_sha256=contract.request_sha256,
            calendar_namespace=namespace,
            calendar_identifier=None,
            events=events,
        )
        result = self._execute(contract, request, control)
        identifier = self._require_completed(contract, request, result)
        if result.event_count != len(events):
            raise self._failure(request.operation, "calendar event verification failed")
        return identifier

    def _cleanup_calendar(
        self,
        contract: CodexAppiumJobContract,
        namespace: str,
        identifier: str,
        events: tuple[CalendarAutomationEvent, ...],
        control: CaptureControl,
    ) -> None:
        request = CalendarAutomationRequest(
            schema_version=_REQUEST_SCHEMA,
            operation=CalendarAutomationOperation.CLEANUP,
            request_sha256=contract.request_sha256,
            calendar_namespace=namespace,
            calendar_identifier=identifier,
            events=events,
        )
        result = self._execute(contract, request, control)
        completed_identifier = self._require_completed(contract, request, result)
        if completed_identifier != identifier or result.event_count != 0:
            raise self._failure(request.operation, "calendar cleanup verification failed")

    def _execute(
        self,
        contract: CodexAppiumJobContract,
        request: CalendarAutomationRequest,
        control: CaptureControl,
    ) -> CalendarAutomationResult:
        return SimctlCalendarAutomationClient(
            xcrun=self.xcrun,
            runner=self.runner,
            poll_interval_seconds=self.poll_interval_seconds,
        ).execute(contract, request, control)

    @staticmethod
    def _require_completed(
        contract: CodexAppiumJobContract,
        request: CalendarAutomationRequest,
        result: CalendarAutomationResult,
    ) -> str:
        identifier = result.calendar_identifier
        if not (
            result.operation is request.operation
            and result.request_sha256 == contract.request_sha256
            and result.calendar_namespace == request.calendar_namespace
            and result.status == "completed"
            and identifier is not None
        ):
            raise SimctlEventKitCalendarDataPort._failure(
                request.operation,
                f"Trace calendar automation failed: {result.error_code or 'result_mismatch'}",
            )
        return identifier

    @staticmethod
    def _failure(
        operation: CalendarAutomationOperation,
        message: str,
    ) -> CaptureAdapterError:
        code = (
            ErrorCode.CALENDAR_PREPARATION_FAILED
            if operation is CalendarAutomationOperation.PREPARE
            else ErrorCode.CALENDAR_CLEANUP_FAILED
        )
        return CaptureAdapterError(code=code, message=message)


def _cleanup_control(control: CaptureControl) -> CaptureControl:
    return CaptureControl(
        expires_at=control.clock.monotonic() + _CLEANUP_TIMEOUT_SECONDS,
        cancel_file=None,
        clock=control.clock,
        sleeper=control.sleeper,
    )


__all__ = [
    "CalendarAutomationOperation",
    "CalendarAutomationRequest",
    "CalendarAutomationResult",
    "CalendarDataPort",
    "CalendarPreparation",
    "SimctlEventKitCalendarDataPort",
    "build_calendar_events",
    "build_todo_calendar_events",
]

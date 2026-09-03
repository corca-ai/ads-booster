from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from ads_booster.capture.calendar_automation_contract import (
    CalendarAutomationOperation,
    CalendarAutomationRequest,
    CalendarAutomationResult,
)
from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.capture.simctl_command import (
    CommandRunner,
    SubprocessCommandRunner,
    parse_app_group_container,
)
from ads_booster.capture.wallpaper_validation import reject_symlink_path
from ads_booster.contracts import ErrorCode

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.capture_safety import CaptureControl
    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract

_REQUEST_FILENAME: Final = "trace_marketing_calendar_request.json"
_RESULT_FILENAME: Final = "trace_marketing_calendar_result.json"


@dataclass(frozen=True, slots=True)
class SimctlCalendarAutomationClient:
    xcrun: str = "xcrun"
    runner: CommandRunner = field(default_factory=SubprocessCommandRunner)
    poll_interval_seconds: float = 0.05

    def execute(
        self,
        contract: CodexAppiumJobContract,
        request: CalendarAutomationRequest,
        control: CaptureControl,
    ) -> CalendarAutomationResult:
        permission = self.runner.run(
            (
                self.xcrun,
                "simctl",
                "privacy",
                contract.device.udid,
                "grant",
                "calendar",
                contract.bundle_id,
            ),
            control.remaining_seconds(),
        )
        if permission.returncode != 0:
            raise _failure(
                request.operation,
                "Trace Calendar access could not be granted on the Simulator",
            )
        control.checkpoint()
        request_path, result_path = self._automation_paths(contract, request.operation, control)
        try:
            result_path.unlink(missing_ok=True)
            _write_private_request(request_path, request)
        except OSError as error:
            raise _failure(
                request.operation,
                "calendar automation files are unavailable",
            ) from error
        launched = self.runner.run(
            (
                self.xcrun,
                "simctl",
                "launch",
                "--terminate-running-process",
                contract.device.udid,
                contract.bundle_id,
                *contract.launch_arguments,
                "-traceMarketingCalendarAutomation",
            ),
            control.remaining_seconds(),
        )
        if launched.returncode != 0:
            raise _failure(
                request.operation,
                f"Trace calendar automation launch failed with exit code {launched.returncode}",
            )
        while not result_path.is_file():
            control.wait(self.poll_interval_seconds)
        try:
            result = CalendarAutomationResult.model_validate_json(
                result_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as error:
            raise _failure(
                request.operation,
                "calendar automation result is invalid",
            ) from error
        control.checkpoint()
        return result

    def _automation_paths(
        self,
        contract: CodexAppiumJobContract,
        operation: CalendarAutomationOperation,
        control: CaptureControl,
    ) -> tuple[Path, Path]:
        completed = self.runner.run(
            (
                self.xcrun,
                "simctl",
                "get_app_container",
                contract.device.udid,
                contract.bundle_id,
                "groups",
            ),
            control.remaining_seconds(),
        )
        if completed.returncode != 0:
            raise _failure(
                operation,
                "Trace App Group lookup failed for calendar automation",
            )
        try:
            container = parse_app_group_container(completed.stdout, contract.app_group_id)
        except CaptureAdapterError as error:
            raise _failure(
                operation,
                "Trace App Group container is unavailable for calendar automation",
            ) from error
        request_path = container / _REQUEST_FILENAME
        result_path = container / _RESULT_FILENAME
        reject_symlink_path(container)
        reject_symlink_path(request_path)
        reject_symlink_path(result_path)
        return request_path, result_path


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


def _write_private_request(
    path: Path,
    request: CalendarAutomationRequest,
) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    reject_symlink_path(temporary)
    try:
        _ = temporary.write_text(request.model_dump_json(), encoding="utf-8")
        temporary.chmod(0o600)
        _ = temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["SimctlCalendarAutomationClient"]

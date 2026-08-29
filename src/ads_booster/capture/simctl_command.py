from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.contracts import ErrorCode


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str
    returncode: int


class CommandRunner(Protocol):
    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class SubprocessCommandRunner:
    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        try:
            completed = subprocess.run(  # noqa: S603
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as error:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=f"simulator command is unavailable: {command[0]}",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise CaptureAdapterError(
                code=ErrorCode.CAPTURE_TIMED_OUT,
                message="simulator command exceeded the capture deadline",
            ) from error
        return CommandResult(stdout=completed.stdout, returncode=completed.returncode)


def parse_app_group_container(output: str, group_id: str) -> Path:
    for line in output.splitlines():
        reported_group, separator, reported_path = line.partition("\t")
        if separator and reported_group == group_id and reported_path:
            return Path(reported_path)
    raise CaptureAdapterError(
        code=ErrorCode.SCENE_CAPTURE_FAILED,
        message=f"Trace App Group container is unavailable: {group_id}",
    )

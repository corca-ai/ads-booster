from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.capture.simctl_command import CommandRunner, SubprocessCommandRunner
from ads_booster.contracts import ErrorCode

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.capture_safety import CaptureControl


@dataclass(frozen=True, slots=True)
class SimctlPhotoImporter:
    xcrun: str = "xcrun"
    runner: CommandRunner = field(default_factory=SubprocessCommandRunner)

    def import_background(
        self,
        udid: str,
        background: Path,
        control: CaptureControl,
    ) -> None:
        if not background.is_file():
            raise CaptureAdapterError(
                code=ErrorCode.INPUT_ASSET_MISSING,
                message="searched wallpaper background is unavailable",
            )
        completed = self.runner.run(
            (self.xcrun, "simctl", "addmedia", udid, str(background)),
            control.remaining_seconds(),
        )
        if completed.returncode != 0:
            raise CaptureAdapterError(
                code=ErrorCode.SCENE_CAPTURE_FAILED,
                message=f"Simulator photo import failed with exit code {completed.returncode}",
            )
        control.checkpoint()

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.capture.simctl_command import CommandResult
from ads_booster.capture.simulator_photo import SimctlPhotoImporter

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class RecordingRunner:
    permission_returncode: int = 0
    commands: list[tuple[str, ...]] = field(default_factory=list)

    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        del timeout_seconds
        self.commands.append(command)
        returncode = self.permission_returncode if "privacy" in command else 0
        return CommandResult(stdout="", returncode=returncode)


def test_import_background_grants_trace_photo_access_before_addmedia(tmp_path: Path) -> None:
    background = tmp_path / "background.png"
    _ = background.write_bytes(b"png")
    runner = RecordingRunner()
    importer = SimctlPhotoImporter(runner=runner)

    importer.import_background(
        "E1FB798D-79E6-4B25-A987-D298A4FD122A",
        background,
        CaptureControl.start(timeout_seconds=5),
    )

    assert runner.commands == [
        (
            "xcrun",
            "simctl",
            "privacy",
            "E1FB798D-79E6-4B25-A987-D298A4FD122A",
            "grant",
            "photos",
            "com.corca.Trace",
        ),
        (
            "xcrun",
            "simctl",
            "addmedia",
            "E1FB798D-79E6-4B25-A987-D298A4FD122A",
            str(background),
        ),
    ]


def test_import_background_stops_when_photo_access_cannot_be_granted(tmp_path: Path) -> None:
    background = tmp_path / "background.png"
    _ = background.write_bytes(b"png")
    runner = RecordingRunner(permission_returncode=1)
    importer = SimctlPhotoImporter(runner=runner)

    with pytest.raises(CaptureAdapterError, match="photo access"):
        importer.import_background(
            "E1FB798D-79E6-4B25-A987-D298A4FD122A",
            background,
            CaptureControl.start(timeout_seconds=5),
        )

    assert len(runner.commands) == 1

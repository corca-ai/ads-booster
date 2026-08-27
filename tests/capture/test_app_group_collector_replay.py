from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.capture.app_group_collector import (
    CommandResult,
    SimctlAppGroupComponentCollector,
)
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    ComponentCollectionRequest,
    ExportBinding,
)
from ads_booster.contracts import ErrorCode

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppGroupRunner:
    container: Path

    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        del command, timeout_seconds
        return CommandResult(
            stdout=f"group.ai.corca.trace\t{self.container}\n",
            returncode=0,
        )


@dataclass(frozen=True, slots=True)
class FixedClock:
    monotonic_value: float
    wall_time_ns: int

    def monotonic(self) -> float:
        return self.monotonic_value

    def time_ns(self) -> int:
        return self.wall_time_ns


class AdvancingClock:
    __slots__: tuple[str, ...] = ("monotonic_value", "wall_time_ns")
    monotonic_value: float
    wall_time_ns: int

    def __init__(self) -> None:
        self.monotonic_value = 0.0
        self.wall_time_ns = 1

    def monotonic(self) -> float:
        return self.monotonic_value

    def time_ns(self) -> int:
        return self.wall_time_ns


@dataclass(frozen=True, slots=True)
class AdvancingSleeper:
    clock: AdvancingClock

    def sleep(self, seconds: float) -> None:
        self.clock.monotonic_value += seconds


class DelayedPublisher:
    __slots__: tuple[str, ...] = ("binding", "calls", "clock", "source")
    clock: AdvancingClock
    source: Path
    binding: ExportBinding
    calls: int

    def __init__(self, clock: AdvancingClock, source: Path, binding: ExportBinding) -> None:
        self.clock = clock
        self.source = source
        self.binding = binding
        self.calls = 0

    def sleep(self, seconds: float) -> None:
        self.calls += 1
        self.clock.monotonic_value += seconds
        if self.calls == 1:
            write_component_png(self.source)
            write_export_manifest(self.source, self.binding)


def write_component_png(path: Path, *, opaque: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (20, 20), (0, 0, 0, 255 if opaque else 0))
    if not opaque:
        for x in range(5, 15):
            for y in range(5, 15):
                image.putpixel((x, y), (255, 255, 255, 255))
    image.save(path, format="PNG")


def write_export_manifest(
    path: Path,
    binding: ExportBinding,
    *,
    role: str = "trace_components",
    width_override: int | None = None,
) -> None:
    with Image.open(path) as image:
        width, height = image.size
    if width_override is not None:
        width = width_override
    content = path.read_bytes()
    _ = path.with_name("trace_components.manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "trace.component-export-manifest.v1",
                "request_sha256": binding.request_sha256,
                "export_nonce": binding.export_nonce,
                "bundle_id": binding.bundle_id,
                "device_udid": binding.device_udid,
                "role": role,
                "artifact_sha256": sha256(content).hexdigest(),
                "width": width,
                "height": height,
            }
        ),
        encoding="utf-8",
    )


def test_collect_when_native_export_is_published_after_first_poll(tmp_path: Path) -> None:
    # Given a native writer that atomically publishes both files after collection begins
    source = tmp_path / "container" / "trace_components.png"
    binding = ExportBinding(
        request_sha256="9" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-delayed",
        cleared_at_ns=0,
    )
    clock = AdvancingClock()
    sleeper = DelayedPublisher(clock, source, binding)
    control = CaptureControl(
        expires_at=5,
        cancel_file=None,
        clock=clock,
        sleeper=sleeper,
    )
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
        clock=clock,
        poll_interval_seconds=1,
    )

    # When collection waits on the shared capture deadline
    provenance = collector.collect(
        ComponentCollectionRequest(
            udid=binding.device_udid,
            destination=tmp_path / "output.png",
            binding=binding,
            control=control,
        ),
    )

    # Then the delayed native publication is accepted without an unbounded wait
    assert sleeper.calls == 2
    assert provenance.session_id == binding.session_id


def test_collect_when_native_export_failure_marker_is_published_then_fails_typed(
    tmp_path: Path,
) -> None:
    # Given the native app has published a typed export failure marker
    container = tmp_path / "container"
    container.mkdir()
    _ = (container / "trace_components.error.json").write_text(
        json.dumps(
            {
                "schema_version": "trace.component-export-failure.v1",
                "code": "export_failed",
                "message": "canonical export could not be written",
            }
        ),
        encoding="utf-8",
    )
    clock = AdvancingClock()
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=container),
        clock=clock,
        poll_interval_seconds=1,
    )
    binding = ExportBinding(
        request_sha256="a" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-failure-marker",
        cleared_at_ns=0,
    )

    # When collection observes the failure marker
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid=binding.device_udid,
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl(
                    expires_at=5,
                    cancel_file=None,
                    clock=clock,
                    sleeper=AdvancingSleeper(clock),
                ),
            )
        )

    # Then it reports the native failure instead of waiting for the capture deadline
    assert raised.value.code is ErrorCode.EXPORT_FAILED
    assert "canonical export" in raised.value.message


def test_collect_when_manifest_canvas_disagrees_with_expected_capture_then_rejects(
    tmp_path: Path,
) -> None:
    # Given a valid component export whose manifest canvas differs from the capture contract
    source = tmp_path / "container" / "trace_components.png"
    write_component_png(source)
    binding = ExportBinding(
        request_sha256="a" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-canvas-mismatch",
        cleared_at_ns=source.stat().st_mtime_ns - 1,
        expected_width=1290,
        expected_height=2796,
    )
    write_export_manifest(source, binding)
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    # When collection validates the artifact against the expected device canvas
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid=binding.device_udid,
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl.start(timeout_seconds=30),
            )
        )

    # Then it rejects the self-consistent but wrong native canvas
    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_capture_control_when_cancel_marker_exists(tmp_path: Path) -> None:
    # Given an external cancellation marker is present
    cancel_file = tmp_path / "cancel"
    _ = cancel_file.touch()
    control = CaptureControl.start(timeout_seconds=30, cancel_file=cancel_file)

    # When a capture boundary checks shared cancellation state
    # Then work stops with a typed cancellation code
    with pytest.raises(CaptureAdapterError) as raised:
        control.checkpoint()
    assert raised.value.code is ErrorCode.CAPTURE_CANCELLED


def test_capture_control_when_deadline_is_expired() -> None:
    # Given the shared deadline has already elapsed
    control = CaptureControl(
        expires_at=10,
        cancel_file=None,
        clock=FixedClock(monotonic_value=11, wall_time_ns=1),
    )

    # When any capture boundary checks remaining time
    # Then work stops with a typed timeout code
    with pytest.raises(CaptureAdapterError) as raised:
        control.checkpoint()
    assert raised.value.code is ErrorCode.CAPTURE_TIMED_OUT

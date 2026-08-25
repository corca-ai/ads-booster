from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from tests.capture.test_app_group_collector_replay import (
    AppGroupRunner,
    write_component_png,
    write_export_manifest,
)
from trace_capture.capture.app_group_collector import SimctlAppGroupComponentCollector
from trace_capture.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    ComponentCollectionRequest,
    ExportBinding,
)
from trace_capture.contracts import ErrorCode

if TYPE_CHECKING:
    from pathlib import Path


def test_collect_when_export_predates_session(tmp_path: Path) -> None:
    # Given an export whose modification time predates the current launch binding
    source = tmp_path / "container" / "trace_components.png"
    write_component_png(source)
    old_ns = 1_700_000_000_000_000_000
    os.utime(source, ns=(old_ns, old_ns))
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )
    binding = ExportBinding(
        request_sha256="c" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-03",
        cleared_at_ns=old_ns + 1,
    )

    # When collection checks freshness
    # Then stale output is rejected with a machine-readable code
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl.start(timeout_seconds=30),
            ),
        )
    assert raised.value.code is ErrorCode.EXPORT_STALE


def test_collect_when_png_is_opaque(tmp_path: Path) -> None:
    # Given a full opaque screenshot mislabeled as a component export
    source = tmp_path / "container" / "trace_components.png"
    write_component_png(source, opaque=True)
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )
    binding = ExportBinding(
        request_sha256="d" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-04",
        cleared_at_ns=source.stat().st_mtime_ns - 1,
    )
    write_export_manifest(source, binding)

    # When component-only postconditions are checked
    # Then the screenshot is rejected rather than recorded as successful
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl.start(timeout_seconds=30),
            ),
        )
    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_collect_when_fully_transparent_region_is_below_twenty_percent(
    tmp_path: Path,
) -> None:
    # Given a PNG whose fully transparent region covers only ten percent
    source = tmp_path / "container" / "trace_components.png"
    source.parent.mkdir(parents=True)
    image = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
    for x in range(20):
        for y in range(2):
            image.putpixel((x, y), (0, 0, 0, 0))
    image.save(source, format="PNG")
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )
    binding = ExportBinding(
        request_sha256="1" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-transparency",
        cleared_at_ns=source.stat().st_mtime_ns - 1,
    )
    write_export_manifest(source, binding)

    # When the compositor-aligned component postcondition is checked
    # Then the export is rejected as insufficiently transparent
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl.start(timeout_seconds=30),
            ),
        )
    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_collect_when_png_has_no_visible_component(tmp_path: Path) -> None:
    # Given an alpha PNG containing no visible Trace pixels
    source = tmp_path / "container" / "trace_components.png"
    source.parent.mkdir(parents=True)
    Image.new("RGBA", (20, 20), (0, 0, 0, 0)).save(source, format="PNG")
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )
    binding = ExportBinding(
        request_sha256="f" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-05",
        cleared_at_ns=source.stat().st_mtime_ns - 1,
    )
    write_export_manifest(source, binding)

    # When component-only postconditions are checked
    # Then an empty transparent canvas is rejected
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl.start(timeout_seconds=30),
            ),
        )
    assert raised.value.code is ErrorCode.EXPORT_INVALID

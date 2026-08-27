from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.capture.app_group_collector import (
    SimctlAppGroupComponentCollector,
)
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    CaptureControl,
    ComponentCollectionRequest,
    ExportBinding,
)
from ads_booster.contracts import ErrorCode
from tests.capture.test_app_group_collector_replay import (
    AppGroupRunner,
    write_component_png,
    write_export_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_collect_when_export_is_fresh_component_png(tmp_path: Path) -> None:
    # Given a fresh native export with both transparent and visible pixels
    source = tmp_path / "container" / "trace_components.png"
    write_component_png(source)
    modified_at_ns = source.stat().st_mtime_ns
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )
    destination = tmp_path / "output" / "components.png"
    binding = ExportBinding(
        request_sha256="b" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-02",
        cleared_at_ns=modified_at_ns - 1,
    )
    write_export_manifest(source, binding)

    # When the collector copies and validates the request/device-bound artifact
    provenance = collector.collect(
        ComponentCollectionRequest(
            udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
            destination=destination,
            binding=binding,
            control=CaptureControl.start(timeout_seconds=30),
        ),
    )

    # Then it returns verifiable artifact provenance
    assert provenance.request_sha256 == "b" * 64
    assert provenance.bundle_id == "com.corca.Trace"
    assert provenance.device_udid == "E1FB798D-79E6-4B25-A987-D298A4FD122A"
    assert provenance.session_id == "appium-session-02"
    assert provenance.width == 20
    assert provenance.height == 20
    assert provenance.artifact_sha256 != ""
    assert provenance.native_export_binding_verified is True
    assert provenance.native_export_nonce == binding.export_nonce


def test_collect_when_native_manifest_is_missing(tmp_path: Path) -> None:
    # Given a fresh component PNG without a native binding witness
    source = tmp_path / "container" / "trace_components.png"
    write_component_png(source)
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )
    binding = ExportBinding(
        request_sha256="e" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-unproven",
        cleared_at_ns=source.stat().st_mtime_ns - 1,
    )
    # When collection attempts to claim the Python-provided provenance
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid=binding.device_udid,
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl.start(timeout_seconds=30),
            ),
        )

    # Then it fails explicitly instead of presenting an unverified success
    assert raised.value.code is ErrorCode.EXPORT_UNVERIFIED


def test_collect_when_source_is_symlink_rejects_resolved_bytes(tmp_path: Path) -> None:
    # Given a native PNG published through a symlinked source path
    real_source = tmp_path / "real" / "trace_components.png"
    write_component_png(real_source)
    source = tmp_path / "container" / "trace_components.png"
    source.parent.mkdir(parents=True)
    source.symlink_to(real_source)
    binding = ExportBinding(
        request_sha256="7" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-symlink-source",
        cleared_at_ns=real_source.stat().st_mtime_ns - 1,
    )
    write_export_manifest(real_source, binding)
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    # When collection sees the symlinked source
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid=binding.device_udid,
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl.start(timeout_seconds=30),
            ),
        )

    # Then it rejects the path instead of accepting resolved bytes
    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_collect_when_manifest_is_symlink_rejects_resolved_metadata(tmp_path: Path) -> None:
    # Given a fresh PNG and a manifest exposed through a symlink
    source = tmp_path / "container" / "trace_components.png"
    write_component_png(source)
    binding = ExportBinding(
        request_sha256="6" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-symlink-manifest",
        cleared_at_ns=source.stat().st_mtime_ns - 1,
    )
    write_export_manifest(source, binding)
    expected_manifest = source.with_name("trace_components.manifest.json")
    real_manifest = source.with_name("manifest-real.json")
    _ = expected_manifest.rename(real_manifest)
    expected_manifest.symlink_to(real_manifest)
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    # When collection waits for a stable native manifest
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid=binding.device_udid,
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl.start(timeout_seconds=30),
            ),
        )

    # Then it rejects symlink metadata before parsing it
    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_collect_when_published_artifact_is_not_png(tmp_path: Path) -> None:
    # Given an atomically present artifact whose bytes are not a PNG
    source = tmp_path / "container" / "trace_components.png"
    source.parent.mkdir(parents=True)
    _ = source.write_bytes(b"not-a-png")
    binding = ExportBinding(
        request_sha256="8" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-malformed",
        cleared_at_ns=source.stat().st_mtime_ns - 1,
    )
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    # When collection validates the published component artifact
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid=binding.device_udid,
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl.start(timeout_seconds=30),
            ),
        )

    # Then malformed bytes are rejected as an invalid export
    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_collect_when_native_manifest_role_or_canvas_does_not_match(
    tmp_path: Path,
) -> None:
    # Given a PNG and a witness whose component role does not match the output
    source = tmp_path / "container" / "trace_components.png"
    write_component_png(source)
    modified_at_ns = source.stat().st_mtime_ns
    binding = ExportBinding(
        request_sha256="a" * 64,
        bundle_id="com.corca.Trace",
        device_udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        session_id="appium-session-role-mismatch",
        cleared_at_ns=modified_at_ns - 1,
    )
    write_export_manifest(source, binding, role="full_screen", width_override=21)
    collector = SimctlAppGroupComponentCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    # When the collector validates the native manifest against the PNG
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(
            ComponentCollectionRequest(
                udid=binding.device_udid,
                destination=tmp_path / "output.png",
                binding=binding,
                control=CaptureControl.start(timeout_seconds=30),
            ),
        )

    # Then it rejects the unsupported role/canvas evidence
    assert raised.value.code is ErrorCode.EXPORT_INVALID

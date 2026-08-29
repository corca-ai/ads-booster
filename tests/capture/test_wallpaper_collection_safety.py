from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.capture.wallpaper_collection import (
    SimctlAppGroupWallpaperCollector,
    WallpaperExportBinding,
)
from ads_booster.contracts import ErrorCode
from ads_booster.contracts.native_export import WallpaperExportManifest

from .test_wallpaper_collection import (
    UDID,
    AppGroupRunner,
    binding_for,
    request_for,
    write_manifest,
    write_wallpaper_png,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_collect_when_manifest_has_wrong_device_rejects(tmp_path: Path) -> None:
    source = tmp_path / "container" / "trace_wallpaper.png"
    write_wallpaper_png(source)
    binding = binding_for(source)
    write_manifest(source, binding)
    manifest_path = source.with_name("trace_wallpaper.manifest.json")
    manifest = WallpaperExportManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    _ = manifest_path.write_text(
        manifest.model_copy(
            update={"device_udid": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"}
        ).model_dump_json(),
        encoding="utf-8",
    )
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_collect_when_export_predates_current_session_rejects(tmp_path: Path) -> None:
    source = tmp_path / "container" / "trace_wallpaper.png"
    write_wallpaper_png(source)
    old_ns = 1_700_000_000_000_000_000
    os.utime(source, ns=(old_ns, old_ns))
    binding = WallpaperExportBinding(
        request_sha256="c" * 64,
        bundle_id="com.corca.Trace",
        device_udid=UDID,
        session_id="appium-wallpaper-stale",
        cleared_at_ns=old_ns + 1,
    )
    write_manifest(source, binding)
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    assert raised.value.code is ErrorCode.EXPORT_STALE


def test_collect_when_manifest_predates_current_session_rejects(tmp_path: Path) -> None:
    source = tmp_path / "container" / "trace_wallpaper.png"
    write_wallpaper_png(source)
    old_ns = 1_700_000_000_000_000_000
    binding = WallpaperExportBinding(
        request_sha256="e" * 64,
        bundle_id="com.corca.Trace",
        device_udid=UDID,
        session_id="appium-wallpaper-stale-manifest",
        cleared_at_ns=old_ns + 1,
    )
    write_manifest(source, binding)
    os.utime(source, ns=(old_ns + 2, old_ns + 2))
    manifest_path = source.with_name("trace_wallpaper.manifest.json")
    os.utime(manifest_path, ns=(old_ns, old_ns))
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    assert raised.value.code is ErrorCode.EXPORT_STALE


def test_collect_when_wallpaper_source_is_symlink_rejects(tmp_path: Path) -> None:
    real_source = tmp_path / "real" / "trace_wallpaper.png"
    write_wallpaper_png(real_source)
    source = tmp_path / "container" / "trace_wallpaper.png"
    source.parent.mkdir()
    source.symlink_to(real_source)
    binding = binding_for(real_source)
    write_manifest(real_source, binding)
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_collect_when_native_failure_marker_is_published_fails_typed(tmp_path: Path) -> None:
    container = tmp_path / "container"
    container.mkdir()
    _ = (container / "trace_wallpaper.error.json").write_text(
        json.dumps(
            {
                "schema_version": "trace.wallpaper-export-failure.v1",
                "code": "export_failed",
                "message": "canonical wallpaper could not be written",
            }
        ),
        encoding="utf-8",
    )
    binding = WallpaperExportBinding(
        request_sha256="d" * 64,
        bundle_id="com.corca.Trace",
        device_udid=UDID,
        session_id="appium-wallpaper-failure",
        cleared_at_ns=0,
    )
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=container),
    )

    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    assert raised.value.code is ErrorCode.EXPORT_FAILED

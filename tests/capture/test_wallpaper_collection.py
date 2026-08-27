from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.capture.app_group_collector import CommandResult
from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.capture.wallpaper_collection import (
    SimctlAppGroupWallpaperCollector,
    WallpaperCollectionRequest,
    WallpaperExportBinding,
)
from ads_booster.contracts import ErrorCode

if TYPE_CHECKING:
    from pathlib import Path


UDID = "E1FB798D-79E6-4B25-A987-D298A4FD122A"


@dataclass(frozen=True, slots=True)
class AppGroupRunner:
    container: Path

    def run(self, command: tuple[str, ...], timeout_seconds: float) -> CommandResult:
        del command, timeout_seconds
        return CommandResult(
            stdout=f"group.ai.corca.trace\t{self.container}\n",
            returncode=0,
        )


def write_wallpaper_png(
    path: Path,
    *,
    transparent: bool = False,
    rgba: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "RGBA" if transparent or rgba else "RGB"
    color = (18, 52, 86, 0) if transparent else (18, 52, 86, 255)
    if mode == "RGB":
        color = color[:3]
    Image.new(mode, (20, 30), color).save(path, format="PNG")


def write_manifest(path: Path, binding: WallpaperExportBinding) -> None:
    with Image.open(path) as image:
        width, height = image.size
    _ = path.with_name("trace_wallpaper.manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "trace.wallpaper-export-manifest.v1",
                "request_sha256": binding.request_sha256,
                "export_nonce": binding.export_nonce,
                "bundle_id": binding.bundle_id,
                "device_udid": binding.device_udid,
                "role": "trace_wallpaper",
                "artifact_sha256": sha256(path.read_bytes()).hexdigest(),
                "width": width,
                "height": height,
            }
        ),
        encoding="utf-8",
    )


def binding_for(path: Path) -> WallpaperExportBinding:
    return WallpaperExportBinding(
        request_sha256="a" * 64,
        bundle_id="com.corca.Trace",
        device_udid=UDID,
        session_id="appium-wallpaper-session",
        cleared_at_ns=path.stat().st_mtime_ns - 1,
    )


def request_for(
    tmp_path: Path,
    binding: WallpaperExportBinding,
) -> WallpaperCollectionRequest:
    return WallpaperCollectionRequest(
        udid=UDID,
        destination=tmp_path / "output" / "wallpaper.png",
        binding=binding,
        control=CaptureControl.start(timeout_seconds=2),
    )


def test_collect_when_fresh_rgb_wallpaper_has_matching_manifest(tmp_path: Path) -> None:
    # Given a native full wallpaper and request-bound manifest in Trace's App Group
    source = tmp_path / "container" / "trace_wallpaper.png"
    write_wallpaper_png(source)
    binding = binding_for(source)
    write_manifest(source, binding)
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    # When the collector reads the stable native export
    provenance = collector.collect(request_for(tmp_path, binding))

    # Then it accepts the opaque wallpaper and preserves verified provenance
    assert provenance.native_export_binding_verified is True
    assert provenance.native_export_nonce == binding.export_nonce
    assert provenance.width == 20
    assert provenance.height == 30
    assert (tmp_path / "output" / "wallpaper.manifest.json").is_file()


def test_collect_when_rgba_wallpaper_has_transparent_pixel_rejects(tmp_path: Path) -> None:
    # Given an otherwise valid export containing transparent wallpaper pixels
    source = tmp_path / "container" / "trace_wallpaper.png"
    write_wallpaper_png(source, transparent=True)
    binding = binding_for(source)
    write_manifest(source, binding)
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    # When opaque full-screen validation runs
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    # Then transparent data cannot be recorded as a full wallpaper
    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_collect_when_rgba_wallpaper_is_fully_opaque_accepts(tmp_path: Path) -> None:
    # Given an RGBA full wallpaper whose alpha channel is fully opaque
    source = tmp_path / "container" / "trace_wallpaper.png"
    write_wallpaper_png(source, rgba=True)
    binding = binding_for(source)
    write_manifest(source, binding)
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    # When the collector validates the native wallpaper
    provenance = collector.collect(request_for(tmp_path, binding))

    # Then the native artifact remains verifiably usable as a full wallpaper
    assert provenance.native_export_binding_verified is True


def test_collect_when_manifest_request_digest_does_not_match_rejects(tmp_path: Path) -> None:
    # Given native bytes whose manifest belongs to a different capture request
    source = tmp_path / "container" / "trace_wallpaper.png"
    write_wallpaper_png(source)
    binding = binding_for(source)
    write_manifest(source, binding)
    manifest_path = source.with_name("trace_wallpaper.manifest.json")
    _ = manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace("a" * 64, "b" * 64),
        encoding="utf-8",
    )
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    # When collection verifies the request binding
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    # Then a different request cannot claim the artifact
    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_collect_when_export_predates_current_session_rejects(tmp_path: Path) -> None:
    # Given a wallpaper left behind before the Appium session cleared the App Group
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

    # When collection checks source freshness
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    # Then stale native output is not reused
    assert raised.value.code is ErrorCode.EXPORT_STALE


def test_collect_when_manifest_predates_current_session_rejects(tmp_path: Path) -> None:
    # Given current wallpaper bytes paired with a manifest from before session clearing
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

    # When the collector verifies all native publication timestamps
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    # Then stale binding metadata cannot authenticate new-looking bytes
    assert raised.value.code is ErrorCode.EXPORT_STALE


def test_collect_when_wallpaper_source_is_symlink_rejects(tmp_path: Path) -> None:
    # Given the App Group path exposes wallpaper bytes through a symlink
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

    # When the collector resolves its export paths
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    # Then symlinked native paths cannot smuggle alternate bytes
    assert raised.value.code is ErrorCode.EXPORT_INVALID


def test_collect_when_native_failure_marker_is_published_fails_typed(tmp_path: Path) -> None:
    # Given Trace's App Group contains a typed full-wallpaper export failure marker
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

    # When collection observes the native terminal result
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    # Then it returns the native failure instead of waiting until timeout
    assert raised.value.code is ErrorCode.EXPORT_FAILED

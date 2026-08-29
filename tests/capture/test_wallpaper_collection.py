from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.capture.capture_safety import CaptureAdapterError, CaptureControl
from ads_booster.capture.simctl_command import CommandResult
from ads_booster.capture.wallpaper_collection import (
    SimctlAppGroupWallpaperCollector,
    WallpaperCollectionRequest,
    WallpaperExportBinding,
)
from ads_booster.capture.wallpaper_validation import read_wallpaper_export_manifest
from ads_booster.contracts import ErrorCode
from ads_booster.contracts.native_export import WallpaperExportManifest

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


def test_read_manifest_when_native_wallpaper_export_is_complete_then_uses_native_export_contract(
    tmp_path: Path,
) -> None:
    # Given a Trace App Group export with a complete native manifest
    source = tmp_path / "container" / "trace_wallpaper.png"
    write_wallpaper_png(source)
    binding = binding_for(source)
    write_manifest(source, binding)

    # When the collector boundary parses its manifest
    manifest = read_wallpaper_export_manifest(source.with_name("trace_wallpaper.manifest.json"))

    # Then the retained native-export contract owns the wallpaper binding type
    assert isinstance(manifest, WallpaperExportManifest)


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


def test_collect_when_manifest_has_wrong_digest_rejects(tmp_path: Path) -> None:
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


def test_collect_when_missing_manifest_rejects_unverified_export(tmp_path: Path) -> None:
    # Given native wallpaper bytes without the request-bound manifest
    source = tmp_path / "container" / "trace_wallpaper.png"
    write_wallpaper_png(source)
    binding = binding_for(source)
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=source.parent),
        manifest_grace_seconds=0,
    )

    # When collection checks native binding evidence
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    # Then result JSON or PNG bytes alone cannot prove success
    assert raised.value.code is ErrorCode.EXPORT_UNVERIFIED


def test_collect_when_manifest_has_wrong_nonce_rejects(tmp_path: Path) -> None:
    # Given native bytes whose manifest carries another execution nonce
    source = tmp_path / "container" / "trace_wallpaper.png"
    write_wallpaper_png(source)
    binding = binding_for(source)
    write_manifest(source, binding)
    manifest_path = source.with_name("trace_wallpaper.manifest.json")
    manifest = WallpaperExportManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    _ = manifest_path.write_text(
        manifest.model_copy(update={"export_nonce": "f" * 64}).model_dump_json(),
        encoding="utf-8",
    )
    collector = SimctlAppGroupWallpaperCollector(
        runner=AppGroupRunner(container=source.parent),
    )

    # When collection validates the native nonce
    with pytest.raises(CaptureAdapterError) as raised:
        _ = collector.collect(request_for(tmp_path, binding))

    # Then an export from another execution is rejected
    assert raised.value.code is ErrorCode.EXPORT_INVALID

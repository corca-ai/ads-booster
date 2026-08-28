from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from ads_booster.capture.appium_codex import CodexAppiumWallpaperExportAdapter
from ads_booster.capture.capture_safety import (
    CaptureAdapterError,
    UdidCaptureLeaseFactory,
)
from ads_booster.capture.factory import build_wallpaper_capture_adapter
from ads_booster.contracts import DeviceKind

from .test_appium_adapter import (
    RecordingPhotoImporter,
    RecordingWallpaperCollector,
    capture_request,
    wallpaper_plan,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


@dataclass(slots=True)
class RecordingCodexJob:
    calls: list[str]
    result: JsonObject
    context: JsonObject | None = None

    def run_appium_job(
        self,
        prompt: str,
        schema: Mapping[str, object],
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        del schema
        assert "CONTROL_PLANE_TOKEN" not in prompt
        assert timeout_seconds > 0
        context_path = workspace / "codex-appium-job.json"
        self.context = json.loads(context_path.read_text(encoding="utf-8"))
        assert stat.S_IMODE(context_path.stat().st_mode) == 0o600
        self.calls.append("codex")
        return self.result


def test_codex_adapter_delegates_live_appium_and_keeps_worker_validation(tmp_path: Path) -> None:
    calls: list[str] = []
    codex = RecordingCodexJob(
        calls,
        {
            "status": "completed",
            "session_id": "appium-session-1",
            "app_group_export_seen": True,
            "cleanup_completed": True,
            "error_code": None,
        },
    )
    adapter = CodexAppiumWallpaperExportAdapter(
        codex=codex,
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        appium_server="http://127.0.0.1:4723",
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
    )
    request = capture_request(tmp_path)

    provenance = adapter.capture(request, wallpaper_plan())

    assert calls == ["clear", "import", "codex", "collect"]
    assert provenance.native_export_binding_verified is True
    assert provenance.session_id == "appium-session-1"
    assert codex.context is not None
    assert codex.context["appium_server"] == "http://127.0.0.1:4723"
    assert codex.context["background"] == str(request.background)
    assert codex.context["plan"] == wallpaper_plan().model_dump(mode="json")
    serialized = json.dumps(codex.context)
    assert "CONTROL_PLANE_TOKEN" not in serialized
    assert "worker_credential" not in serialized


def test_codex_adapter_rejects_unfinished_cleanup_before_collection(tmp_path: Path) -> None:
    calls: list[str] = []
    adapter = CodexAppiumWallpaperExportAdapter(
        codex=RecordingCodexJob(
            calls,
            {
                "status": "completed",
                "session_id": "appium-session-1",
                "app_group_export_seen": True,
                "cleanup_completed": False,
                "error_code": None,
            },
        ),
        simulator=RecordingPhotoImporter(calls),
        collector=RecordingWallpaperCollector(calls),
        appium_server="http://127.0.0.1:4723",
        lease_factory=UdidCaptureLeaseFactory(tmp_path / "leases"),
    )

    with pytest.raises(CaptureAdapterError, match="Codex Appium job did not complete"):
        _ = adapter.capture(capture_request(tmp_path), wallpaper_plan())

    assert calls == ["clear", "import", "codex"]


def test_wallpaper_factory_selects_codex_operator_for_production_path() -> None:
    codex = RecordingCodexJob([], {})

    adapter = build_wallpaper_capture_adapter(
        DeviceKind.SIMULATOR,
        "http://127.0.0.1:4723",
        codex=codex,
    )

    assert isinstance(adapter, CodexAppiumWallpaperExportAdapter)
    assert adapter.codex is codex

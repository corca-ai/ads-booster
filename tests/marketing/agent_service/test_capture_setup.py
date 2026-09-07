"""Optional installed capture is discoverable only through current read-only readiness."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ads_booster.capture.readiness import DefaultCaptureReadiness
from ads_booster.contracts.tool_capability import ToolReadiness
from ads_booster.marketing.agent_core.registry import CapabilityPolicy
from ads_booster.marketing.agent_service.capture_readiness import CaptureReadinessProbe
from ads_booster.marketing.agent_service.lifecycle import (
    InstalledServicePaths,
    build_installed_marketing_agent_service,
)

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def test_opt_in_catalog_never_boots_devices_and_rechecks_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = [True]

    def check(self: CaptureReadinessProbe, *, now: datetime) -> ToolReadiness:
        _ = self
        return ToolReadiness(
            ready=ready[0],
            observed_at=now,
            max_age_seconds=30,
            reason_code=None if ready[0] else "fixture_appium_missing",
        )

    def forbidden(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        pytest.fail("catalog discovery attempted device preparation")

    monkeypatch.setattr(CaptureReadinessProbe, "check", check)
    monkeypatch.setattr(DefaultCaptureReadiness, "ensure", forbidden)
    config = tmp_path / "capture.json"
    _ = config.write_text(
        json.dumps(
            {
                "device": {
                    "kind": "simulator",
                    "udid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
                    "platform_version": "18.0",
                    "device_name": "fixture device",
                }
            }
        )
    )
    service = build_installed_marketing_agent_service(
        paths=InstalledServicePaths(tmp_path / "service"),
        codex_executable=Path("/fixture/codex"),
        model_id="fixture",
        timeout_seconds=300,
        capture_config=config,
    )
    descriptor = next(
        item
        for item in service.registry.current_descriptors(now=NOW)
        if item.capability_id == "capture.appium"
    )
    assert descriptor.readiness.ready
    assert descriptor.approval_policy.mode != "none"
    assert "capture.appium" in service.tools
    ready[0] = False
    with pytest.raises(ValueError, match="tool_dispatch_no_longer_available"):
        _ = service.registry.require_current_dispatch(
            descriptor, policy=CapabilityPolicy(), now=NOW
        )
    assert not (tmp_path / "service/artifacts/capture-jobs").exists()


def test_default_service_does_not_probe_or_register_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        _ = args, kwargs
        pytest.fail("default service probed capture")

    monkeypatch.setattr(CaptureReadinessProbe, "check", forbidden)
    service = build_installed_marketing_agent_service(
        paths=InstalledServicePaths(tmp_path),
        codex_executable=Path("/fixture/codex"),
        model_id="fixture",
        timeout_seconds=300,
    )
    assert "capture.appium" not in service.tools

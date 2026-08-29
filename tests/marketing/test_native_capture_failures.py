from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, override

import pytest

from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.native_capture import HostedWorkspaceCaptureExecutor
from tests.marketing.test_native_capture import (
    FakeDeviceResolver,
    RecordingAppiumAdapter,
    RecordingBackgroundPreparer,
    build_executor,
    task_fixture,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.capture_safety import CaptureControl
    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract
    from ads_booster.contracts.models import CaptureProvenance


ArtifactFailure = Literal["missing", "manifest", "size", "provenance", "digest"]


@dataclass(slots=True, kw_only=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class ValidationFailureAppiumAdapter(RecordingAppiumAdapter):
    artifact_failure: ArtifactFailure

    @override
    def execute(
        self,
        contract: CodexAppiumJobContract,
        *,
        job_root: Path,
        background: Path,
        output: Path,
        control: CaptureControl,
    ) -> CaptureProvenance:
        provenance = super().execute(
            contract,
            job_root=job_root,
            background=background,
            output=output,
            control=control,
        )
        if self.artifact_failure == "missing":
            output.unlink()
        if self.artifact_failure == "manifest":
            _ = output.with_suffix(".manifest.json").write_text("invalid")
        if self.artifact_failure == "size":
            _ = output.write_bytes(b"")
        if self.artifact_failure == "provenance":
            provenance = provenance.model_copy(update={"bundle_id": "com.example.Other"})
        if self.artifact_failure == "digest":
            manifest_path = output.with_suffix(".manifest.json")
            _ = manifest_path.write_text(
                manifest_path.read_text().replace(provenance.artifact_sha256, "f" * 64)
            )
        return provenance


def test_missing_background_after_admission_is_unknown_side_effect(tmp_path: Path) -> None:
    calls: list[str] = []
    executor = build_executor(tmp_path, calls)
    prepared = executor.prepare(task_fixture())
    prepared.background.unlink()

    with pytest.raises(
        MarketingExecutionError, match="native_capture_background_missing"
    ) as raised:
        _ = executor.execute(prepared)

    assert raised.value.unknown_side_effect is True
    assert calls.count("execute") == 0


@pytest.mark.parametrize(
    ("failure", "failure_code"),
    [
        ("missing", "native_capture_artifact_missing"),
        ("manifest", "native_capture_artifact_missing"),
        ("size", "native_capture_artifact_size_invalid"),
        ("provenance", "native_capture_provenance_unverified"),
        ("digest", "native_capture_artifact_digest_mismatch"),
    ],
)
def test_post_appium_validation_failure_is_unknown_side_effect(
    tmp_path: Path,
    failure: ArtifactFailure,
    failure_code: str,
) -> None:
    calls: list[str] = []
    executor = HostedWorkspaceCaptureExecutor(
        background_preparer=RecordingBackgroundPreparer(calls),
        appium=ValidationFailureAppiumAdapter(calls=calls, artifact_failure=failure),
        output_root=tmp_path / "generated",
        device_resolver=FakeDeviceResolver(),
    )
    prepared = executor.prepare(task_fixture())

    with pytest.raises(MarketingExecutionError, match=failure_code) as raised:
        _ = executor.execute(prepared)

    assert raised.value.unknown_side_effect is True
    assert calls.count("execute") == 1

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.contracts.feedback import FeedbackContext, feedback_context_sha256
from ads_booster.contracts.models import CaptureProvenance, DeviceKind, DeviceTarget
from ads_booster.contracts.native_export import (
    PreparedBackground,
    TraceBackgroundSearchProvenance,
    WallpaperExportManifest,
)
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind
from ads_booster.marketing.native_capture import (
    HostedWorkspaceCaptureExecutor,
    SimctlDeviceResolver,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.capture.capture_safety import CaptureControl
    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract
    from ads_booster.contracts.generation import MarketingContextBundle


class FakeDeviceResolver:
    def resolve(self) -> DeviceTarget:
        return DeviceTarget(
            kind=DeviceKind.SIMULATOR,
            udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
            platform_version="26.5",
            device_name="iPhone 17 Pro",
        )


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class RecordingBackgroundPreparer:
    """Mutable fixture records preparation calls for the hosted-capture assertion."""

    calls: list[str]

    def prepare(self, bundle: MarketingContextBundle, job_root: Path) -> PreparedBackground:
        del bundle
        self.calls.append("background")
        path = job_root / "inputs" / "background.png"
        path.parent.mkdir(parents=True)
        _ = path.write_bytes(b"prepared-background")
        digest = sha256(path.read_bytes()).hexdigest()
        return PreparedBackground(
            path="inputs/background.png",
            sha256=digest,
            provenance=TraceBackgroundSearchProvenance(
                schema_version="trace.background-search.v1",
                artifact_path="inputs/background.png",
                artifact_sha256=digest,
                query="campus morning",
                provider="test-search",
                image_url="https://images.pexels.com/photo/1",
                source_url="https://www.pexels.com/photo/1",
            ),
        )


@dataclass(slots=True)  # noqa: RUF100  # noqa: MUTABLE_OK
class RecordingAppiumAdapter:
    """Mutable fixture records Appium execution and test-controlled output mutation."""

    calls: list[str]
    execute_calls: int = 0
    mutate_background: bool = False

    def ensure_ready(self, contract: CodexAppiumJobContract, control: CaptureControl) -> None:
        assert contract.prepared_background.sha256
        control.checkpoint()
        self.calls.append("ready")

    def execute(
        self,
        contract: CodexAppiumJobContract,
        *,
        job_root: Path,
        background: Path,
        output: Path,
        control: CaptureControl,
    ) -> CaptureProvenance:
        del job_root
        self.calls.append("execute")
        self.execute_calls += 1
        control.checkpoint()
        if self.mutate_background:
            _ = background.write_bytes(b"changed-after-admission")
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (12, 20), "blue").save(output, format="PNG")
        image = output.read_bytes()
        digest = sha256(image).hexdigest()
        manifest = WallpaperExportManifest(
            schema_version="trace.wallpaper-export-manifest.v1",
            request_sha256=contract.request_sha256,
            export_nonce=contract.export_nonce,
            bundle_id=contract.bundle_id,
            device_udid=contract.device.udid,
            role="trace_wallpaper",
            artifact_sha256=digest,
            width=12,
            height=20,
        )
        _ = output.with_suffix(".manifest.json").write_text(manifest.model_dump_json())
        return CaptureProvenance(
            request_sha256=contract.request_sha256,
            artifact_sha256=digest,
            bundle_id=contract.bundle_id,
            device_udid=contract.device.udid,
            session_id="native-session",
            byte_size=len(image),
            width=12,
            height=20,
            source_modified_at_ns=1,
            source="native_appium",
            artifact_role="trace_wallpaper",
            native_export_nonce=contract.export_nonce,
            native_export_binding_verified=True,
        )


def task_fixture() -> MarketingTask:
    feedback = FeedbackContext.model_validate(
        {
            "schema_version": "trace.feedback-context.v1",
            "stage": "image",
            "scope": {"account_id": "trace_demo_kr", "context_profile_id": None},
            "rules": [],
            "immediate_correction": {
                "source_event_id": "event-1",
                "source_candidate_id": "candidate-1",
                "source_candidate_revision": 3,
                "source_capture_task_id": "capture-previous",
                "source_artifact_sha256": "a" * 64,
                "rating": 2,
                "tags": ["앱 화면·데이터 오류"],
                "note": "일정 한 줄이 승인본과 다릅니다.",
            },
        }
    )
    return MarketingTask(
        task_id="c7dcc5a4-d841-49d0-bd34-f94afef98485",
        run_id="66dcd684-2e69-4cf1-bbf3-da3684102299",
        account_id="trace_demo_kr",
        kind=TaskKind.CAPTURE,
        idempotency_key="hosted:trace_demo_kr:candidate-1:3",
        payload={
            "pipeline": "hosted_workspace_capture_v1",
            "workspace_id": "workspace-1",
            "candidate_id": "candidate-1",
            "candidate_revision": 4,
            "country": "KR",
            "topic": "대학생의 하루",
            "caption": "오늘 일정",
            "hypothesis": "시험 일정을 구체적으로 보여주면 공감이 생긴다.",
            "reference_ids": ["kr-study-day", "kr-020"],
            "creative_direction": "실제 캠퍼스 아침처럼 자연스럽게 구성",
            "background_intent": "scenery: 이른 아침 캠퍼스",
            "feedback_context": feedback.model_dump(mode="json"),
            "feedback_context_sha256": feedback_context_sha256(feedback),
            "image_inputs": {
                "trace_items": ["09:00 통계학", "13:00 스터디"],
                "device_time": "07:20",
                "background_mood": "이른 아침 캠퍼스",
                "language": "ko",
            },
            "context_profile": {
                "name": "한국 대학생 프로필",
                "persona_id": "kr_student",
                "audience": "한국 대학생",
                "situation": "평일 캠퍼스",
                "tone": "자연스러움",
            },
        },
        created_at=datetime.now(UTC),
    )


def build_executor(
    tmp_path: Path,
    calls: list[str],
    *,
    mutate: bool = False,
) -> HostedWorkspaceCaptureExecutor:
    return HostedWorkspaceCaptureExecutor(
        background_preparer=RecordingBackgroundPreparer(calls),
        appium=RecordingAppiumAdapter(calls, mutate_background=mutate),
        output_root=tmp_path / "generated",
        device_resolver=FakeDeviceResolver(),
    )


def test_hosted_capture_contract_has_no_imagegen_postprocess_seam() -> None:
    field_names = {field.name for field in fields(HostedWorkspaceCaptureExecutor)}

    assert "image_editor" not in field_names


def test_hosted_capture_prepares_planless_job_before_execution(tmp_path: Path) -> None:
    calls: list[str] = []
    executor = build_executor(tmp_path, calls)

    prepared = executor.prepare(task_fixture())

    assert calls == ["background", "ready"]
    assert prepared.execution_admission.job_digest == prepared.contract.request_sha256
    assert prepared.execution_admission.export_nonce == prepared.contract.export_nonce
    assert prepared.execution_admission.workspace_id == "workspace-1"
    assert prepared.contract.context.feedback_context is not None
    correction = prepared.contract.context.feedback_context.immediate_correction
    assert correction is not None
    assert correction.source_event_id == "event-1"


def test_hosted_capture_executes_once_and_independently_verifies_callback_png(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    executor = build_executor(tmp_path, calls)
    prepared = executor.prepare(task_fixture())

    result = executor.execute(prepared)

    image = prepared.output.read_bytes()
    digest = sha256(image).hexdigest()
    assert calls == ["background", "ready", "execute"]
    assert result.output["capture_source"] == "native_appium"
    assert result.output["image_postprocess_source"] == "none"
    assert result.output["native_image_sha256"] == digest
    assert result.output["image_sha256"] == digest
    assert result.output["feedback_application_sha256"] == (
        prepared.contract.context.feedback_context_sha256
    )
    assert base64.b64decode(str(result.output["image_base64"])) == image
    manifest = WallpaperExportManifest.model_validate_json(
        prepared.output.with_suffix(".manifest.json").read_text()
    )
    assert manifest.artifact_sha256 == digest
    assert manifest.request_sha256 == prepared.contract.request_sha256


def test_changed_background_fails_after_admission_without_second_codex_call(tmp_path: Path) -> None:
    calls: list[str] = []
    executor = build_executor(tmp_path, calls)
    prepared = executor.prepare(task_fixture())
    _ = prepared.background.write_bytes(b"changed-after-admission")

    with pytest.raises(
        MarketingExecutionError,
        match="native_capture_background_digest_mismatch",
    ) as raised:
        _ = executor.execute(prepared)

    assert raised.value.unknown_side_effect is True
    assert calls.count("execute") == 0


def test_invalid_hosted_bundle_fails_before_readiness(tmp_path: Path) -> None:
    calls: list[str] = []
    task = task_fixture().model_copy(
        update={"payload": {**task_fixture().payload, "image_inputs": {}}}
    )

    with pytest.raises(MarketingExecutionError, match="native_capture_trace_items_invalid"):
        _ = build_executor(tmp_path, calls).prepare(task)

    assert calls == []


def test_simulator_is_discovered_at_runtime_without_a_fixed_udid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
                {
                    "name": "iPhone 17 Pro",
                    "udid": "E1FB798D-79E6-4B25-A987-D298A4FD122A",
                    "state": "Booted",
                    "isAvailable": True,
                },
            ],
        },
    }

    def fake_run(*_args: str, **_kwargs: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=(), returncode=0, stdout=json.dumps(inventory))

    monkeypatch.setattr(subprocess, "run", fake_run)

    device = SimctlDeviceResolver().resolve()

    assert device.device_name == "iPhone 17 Pro"
    assert device.platform_version == "26.5"
    assert device.udid == "E1FB798D-79E6-4B25-A987-D298A4FD122A"

from __future__ import annotations

import base64
import json
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, cast

import pytest

from ads_booster.connectors.trace.v1.codex_runtime import CodexWallpaperPlanner
from ads_booster.contracts.models import CaptureProvenance, DeviceKind, DeviceTarget
from ads_booster.contracts.results import TraceRunResult
from ads_booster.contracts.run import TraceRunState
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind
from ads_booster.marketing.native_capture import (
    HostedWorkspaceCaptureExecutor,
    SimctlDeviceResolver,
)
from tests.connectors.trace.v1.test_connector import plan

_LONG_TOPIC = "T" * 200
_LONG_AUDIENCE = ("global audience " * 30).strip()
_LONG_SITUATION = ("weekday context " * 25).strip()
_LONG_TONE = ("observational tone " * 12).strip()


if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.providers.codex_cli import CodexCli


class RecordingCodexClient:
    def __init__(self) -> None:
        self.prompt: str = ""

    def generate_json(
        self,
        prompt: str,
        schema: dict[str, object],
        *,
        images: tuple[Path, ...] = (),
    ) -> dict[str, object]:
        _ = (schema, images)
        self.prompt = prompt
        return plan().model_dump(mode="json")


class FakeDeviceResolver:
    def resolve(self) -> DeviceTarget:
        return DeviceTarget(
            kind=DeviceKind.SIMULATOR,
            udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
            platform_version="26.5",
            device_name="iPhone 17 Pro",
        )


class FakeNativeRunner:
    def __init__(self, output_root: Path, image: bytes) -> None:
        self.output_root: Path = output_root
        self.image: bytes = image
        self.bundle: MarketingContextBundle | None = None

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        self.bundle = bundle
        output = self.output_root / bundle.request_id / "outputs" / "final.png"
        component = self.output_root / bundle.request_id / "work" / "trace-components.png"
        output.parent.mkdir(parents=True)
        component.parent.mkdir(parents=True)
        _ = output.write_bytes(self.image)
        _ = component.write_bytes(b"component")
        return TraceRunResult(
            run_id=bundle.request_id,
            idempotency_key=f"{bundle.request_id}-v1",
            input_digest="a" * 64,
            state=TraceRunState.COMPLETED,
            component_artifact="work/trace-components.png",
            component_artifact_sha256=sha256(b"component").hexdigest(),
            output_image="outputs/final.png",
            output_image_sha256=sha256(self.image).hexdigest(),
            capture_provenance=CaptureProvenance(
                request_sha256="b" * 64,
                artifact_sha256=sha256(b"component").hexdigest(),
                bundle_id="com.corca.Trace",
                device_udid=bundle.device.udid,
                session_id="native-session",
                byte_size=len(b"component"),
                width=1206,
                height=2622,
                source_modified_at_ns=1,
                source="native_appium",
                native_export_nonce="c" * 64,
                native_export_binding_verified=True,
            ),
        )


class UnknownSideEffectRunner:
    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        return TraceRunResult(
            schema_version="trace.run-result.v2",
            run_id=bundle.request_id,
            idempotency_key=f"{bundle.request_id}-v2",
            input_digest="a" * 64,
            state=TraceRunState.UNKNOWN_SIDE_EFFECT,
        )


def _task() -> MarketingTask:
    return MarketingTask(
        task_id="c7dcc5a4-d841-49d0-bd34-f94afef98485",
        run_id="66dcd684-2e69-4cf1-bbf3-da3684102299",
        account_id="trace_demo_kr",
        kind=TaskKind.CAPTURE,
        idempotency_key="hosted:trace_demo_kr:candidate-1:3",
        payload={
            "pipeline": "hosted_workspace_capture_v1",
            "candidate_id": "candidate-1",
            "candidate_revision": 4,
            "country": "KR",
            "topic": _LONG_TOPIC,
            "caption": "오늘 일정",
            "hypothesis": "시험 일정을 구체적으로 보여주면 공감이 생긴다.",
            "reference_ids": ["kr-study-day", "kr-020"],
            "creative_direction": "실제 캠퍼스 아침처럼 자연스럽게 구성",
            "background_intent": "scenery: 이른 아침 캠퍼스",
            "image_inputs": {
                "trace_items": ["09:00 통계학", "13:00 스터디"],
                "device_time": "07:20",
                "background_subject": "scenery",
                "background_mood": "이른 아침 캠퍼스",
                "language": "ko",
            },
            "context_profile": {
                "name": "한국 대학생 프로필",
                "persona_id": "kr_student",
                "audience": _LONG_AUDIENCE,
                "situation": _LONG_SITUATION,
                "tone": _LONG_TONE,
                "guidance": "과장 없이 실제 사용 장면을 보여준다.",
                "reference_ids": ["kr-020"],
            },
        },
        created_at=datetime.now(UTC),
    )


def test_hosted_capture_returns_digest_backed_png_for_cloudflare(tmp_path: Path) -> None:
    image = b"\x89PNG\r\n\x1a\ntrace-native-image"
    runner = FakeNativeRunner(tmp_path / "generated", image)
    executor = HostedWorkspaceCaptureExecutor(
        runner=runner,
        output_root=tmp_path / "generated",
        plan_root=tmp_path / "codex-runs",
        device_resolver=FakeDeviceResolver(),
    )

    request_id = _task().task_id
    plan_root = tmp_path / "codex-runs" / request_id
    plan_root.mkdir(parents=True)
    plan_path = plan_root / "plan.json"
    _ = plan_path.write_text(plan().model_copy(update={"request_id": request_id}).model_dump_json())
    background_image = b"normalized-background-png"
    background_image_path = tmp_path / "generated" / request_id / "inputs" / "background.png"
    background_image_path.parent.mkdir(parents=True, exist_ok=True)
    _ = background_image_path.write_bytes(background_image)
    background_path = tmp_path / "generated" / request_id / "inputs" / "background-source.json"
    background_path.parent.mkdir(parents=True, exist_ok=True)
    _ = background_path.write_text(
        json.dumps(
            {
                "schema_version": "trace.background-search.v1",
                "query": "Seoul campus morning",
                "provider": "image-search",
                "source_url": "https://www.pexels.com/photo/background",
                "image_url": "https://images.pexels.com/background.png",
                "artifact_sha256": sha256(background_image).hexdigest(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )

    result = executor.execute(_task())

    assert result.output["capture_source"] == "native_appium"
    assert result.output["image_sha256"] == sha256(image).hexdigest()
    assert base64.b64decode(str(result.output["image_base64"])) == image
    provenance = cast("dict[str, object]", result.output["generation_provenance"])
    plan_receipt = cast("dict[str, object]", provenance["plan"])
    background_receipt = cast("dict[str, object]", provenance["background"])
    assert provenance["schema_version"] == "trace.hosted-generation-provenance.v1"
    assert plan_receipt["request_id"] == request_id
    assert (
        provenance["plan_sha256"]
        == sha256(
            json.dumps(
                plan_receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert background_receipt["source_url"] == "https://www.pexels.com/photo/background"
    assert background_receipt["artifact_sha256"] == sha256(background_image).hexdigest()
    assert (
        provenance["background_sha256"]
        == sha256(
            json.dumps(
                background_receipt,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    assert runner.bundle is not None
    assert runner.bundle.persona.persona_id == "kr_student"
    assert runner.bundle.promotion_material.trace_items == ("09:00 통계학", "13:00 스터디")
    assert runner.bundle.promotion_material.caption == "오늘 일정"
    assert (
        runner.bundle.promotion_material.hypothesis
        == "시험 일정을 구체적으로 보여주면 공감이 생긴다."
    )
    assert runner.bundle.promotion_material.reference_ids == ("kr-study-day", "kr-020")
    assert (
        runner.bundle.promotion_material.creative_direction
        == "실제 캠퍼스 아침처럼 자연스럽게 구성"
    )
    assert runner.bundle.promotion_material.background_intent == "scenery: 이른 아침 캠퍼스"
    assert runner.bundle.persona.display_name == "한국 대학생 프로필"
    assert runner.bundle.promotion_material.concept == _LONG_TOPIC
    assert runner.bundle.persona.occupation == _LONG_AUDIENCE
    assert runner.bundle.persona.traits == (_LONG_TONE,)
    assert runner.bundle.persona.interests == (_LONG_SITUATION,)
    client = RecordingCodexClient()
    _ = CodexWallpaperPlanner(
        client=cast("CodexCli", cast("object", client)),
        reference_root=tmp_path,
    ).plan(runner.bundle)
    assert _LONG_TOPIC in client.prompt
    assert "한국 대학생 프로필" in client.prompt
    assert _LONG_AUDIENCE in client.prompt
    assert _LONG_SITUATION in client.prompt
    assert _LONG_TONE in client.prompt
    assert runner.bundle.device.udid == "E1FB798D-79E6-4B25-A987-D298A4FD122A"


def test_hosted_capture_preserves_an_unknown_native_side_effect(tmp_path: Path) -> None:
    executor = HostedWorkspaceCaptureExecutor(
        runner=UnknownSideEffectRunner(),
        output_root=tmp_path / "generated",
        device_resolver=FakeDeviceResolver(),
    )

    with pytest.raises(MarketingExecutionError) as failure:
        _ = executor.execute(_task())

    assert failure.value.failure_code == "native_appium_side_effect_unknown"
    assert failure.value.unknown_side_effect is True


def test_hosted_capture_rejects_tampered_background_provenance(tmp_path: Path) -> None:
    image = b"\x89PNG\r\n\x1a\ntrace-native-image"
    runner = FakeNativeRunner(tmp_path / "generated", image)
    request_id = _task().task_id
    plan_root = tmp_path / "codex-runs" / request_id
    plan_root.mkdir(parents=True)
    _ = (plan_root / "plan.json").write_text(
        plan().model_copy(update={"request_id": request_id}).model_dump_json()
    )
    inputs = tmp_path / "generated" / request_id / "inputs"
    inputs.mkdir(parents=True)
    _ = (inputs / "background.png").write_bytes(b"actual-background")
    _ = (inputs / "background-source.json").write_text(
        json.dumps(
            {
                "schema_version": "trace.background-search.v1",
                "query": "Seoul campus morning",
                "provider": "image-search",
                "image_url": "https://images.pexels.com/background.png",
                "source_url": "https://www.pexels.com/photo/background",
                "artifact_sha256": sha256(b"different-background").hexdigest(),
            }
        )
    )
    executor = HostedWorkspaceCaptureExecutor(
        runner=runner,
        output_root=tmp_path / "generated",
        plan_root=tmp_path / "codex-runs",
        device_resolver=FakeDeviceResolver(),
    )

    with pytest.raises(
        MarketingExecutionError,
        match="native_capture_background_artifact_digest_mismatch",
    ):
        _ = executor.execute(_task())


def test_hosted_capture_rejects_an_invalid_candidate_contract(tmp_path: Path) -> None:
    task = _task().model_copy(update={"payload": {**_task().payload, "image_inputs": {}}})
    executor = HostedWorkspaceCaptureExecutor(
        runner=FakeNativeRunner(tmp_path / "generated", b"image"),
        output_root=tmp_path / "generated",
        device_resolver=FakeDeviceResolver(),
    )

    with pytest.raises(MarketingExecutionError, match="native_capture_trace_items_invalid"):
        _ = executor.execute(task)


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
        return subprocess.CompletedProcess(
            args=(),
            returncode=0,
            stdout=json.dumps(inventory),
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    device = SimctlDeviceResolver().resolve()

    assert device.device_name == "iPhone 17 Pro"
    assert device.platform_version == "26.5"
    assert device.udid == "E1FB798D-79E6-4B25-A987-D298A4FD122A"

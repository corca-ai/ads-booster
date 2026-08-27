from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.candidate_generation import CandidateImageRunner, CandidateImageStageError
from ads_booster.contracts import CaptureProvenance
from ads_booster.contracts.models import DeviceKind, DeviceTarget
from ads_booster.contracts.results import TraceRunResult
from ads_booster.contracts.run import TraceRunState
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.workspace import (
    CandidateBackgroundProvenance,
    CandidateCreate,
    CandidateImageAttachment,
    CandidateImageInputs,
    CandidateImagePipeline,
    CandidateSource,
    CandidateStatus,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.workspace import CandidateId, CandidateRecord, WorkspaceId


def _inputs() -> CandidateImageInputs:
    return CandidateImageInputs(
        trace_items=("09:00 통계학", "13:00 스터디", "19:00 러닝"),
        device_time="07:20",
        background_intent="늦은 밤 책상 위 스탠드 불빛이 보이는 실제 공부방 사진",
        language="ko",
    )


def _workspace(store: SqliteWorkspaceStore) -> WorkspaceId:
    return store.create_workspace("Trace team").workspace.workspace_id


def _candidate(store: SqliteWorkspaceStore, workspace_id: WorkspaceId) -> CandidateRecord:
    created = store.create_candidate(
        CandidateCreate(
            workspace_id=workspace_id,
            source=CandidateSource.AUTO,
            country="KR",
            topic="시험기간 일정 관리",
            caption="시험 기간엔 잠금화면부터 바꾼다",
            hypothesis="1인칭 상황 묘사가 공감을 만든다",
            image_inputs=_inputs(),
            refs_used=("kr-study-day",),
            shooting_order="저녁 공부방의 현실적인 질감을 참고",
        )
    )
    return store.review_candidate(
        workspace_id,
        created.candidate_id,
        accepted=True,
        note=None,
        expected_revision=created.revision,
    )


def _device() -> DeviceTarget:
    return DeviceTarget(
        kind=DeviceKind.SIMULATOR,
        udid="E1FB798D-79E6-4B25-A987-D298A4FD122A",
        platform_version="26.5",
        device_name="iPhone 17 Pro",
    )


@dataclass(frozen=True, slots=True)
class FixedDeviceResolver:
    failure: MarketingExecutionError | None = None

    def resolve(self) -> DeviceTarget:
        if self.failure is not None:
            raise self.failure
        return _device()


@dataclass(slots=True)  # noqa: MUTABLE_OK
class RecordingCoreRunner:
    home: Path
    state: TraceRunState = TraceRunState.COMPLETED
    corrupt_digest: bool = False
    bundles: list[MarketingContextBundle] = field(default_factory=list)

    def run(self, bundle: MarketingContextBundle) -> TraceRunResult:
        self.bundles.append(bundle)
        if self.state is not TraceRunState.COMPLETED:
            return TraceRunResult(
                run_id=bundle.request_id,
                idempotency_key=f"{bundle.request_id}-v1",
                input_digest="b" * 64,
                state=self.state,
            )
        image = self.home / "generated" / bundle.request_id / "outputs" / "final.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        _ = image.write_bytes(b"native candidate image")
        digest = sha256(image.read_bytes()).hexdigest()
        return TraceRunResult(
            run_id=bundle.request_id,
            idempotency_key=f"{bundle.request_id}-v1",
            input_digest="b" * 64,
            state=TraceRunState.COMPLETED,
            component_artifact="work/trace-components.png",
            component_artifact_sha256="a" * 64,
            output_image="outputs/final.png",
            output_image_sha256="f" * 64 if self.corrupt_digest else digest,
            capture_provenance=CaptureProvenance(
                request_sha256="c" * 64,
                artifact_sha256="a" * 64,
                bundle_id="com.corca.Trace",
                device_udid=_device().udid,
                session_id="appium-session",
                byte_size=1024,
                width=1206,
                height=2622,
                source_modified_at_ns=1,
                source="native_appium",
                native_export_nonce="d" * 64,
                native_export_binding_verified=True,
            ),
        )


def _runner(
    root: Path,
    store: SqliteWorkspaceStore,
    core: RecordingCoreRunner,
    resolver: FixedDeviceResolver | None = None,
) -> CandidateImageRunner:
    return CandidateImageRunner(
        store=store,
        runner=core,
        device_resolver=resolver or FixedDeviceResolver(),
        home=root,
        clock=lambda: datetime(2026, 8, 26, 9, 30, tzinfo=UTC),
    )


def test_candidate_image_uses_agent_without_inventing_persona_fields(tmp_path: Path) -> None:
    # Given a reviewed candidate and a native Trace execution boundary
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _candidate(store, workspace_id)
    core = RecordingCoreRunner(tmp_path)

    # When the image stage executes
    reviewed = _runner(tmp_path, store, core).generate(workspace_id, candidate.candidate_id)

    # Then all supplied creative context reaches Core while absent persona facts remain absent
    bundle = core.bundles[0]
    assert bundle.persona.age_group is None
    assert bundle.persona.occupation is None
    assert bundle.persona.traits == ()
    assert bundle.persona.interests == ()
    assert bundle.promotion_material.trace_items == _inputs().trace_items
    assert bundle.promotion_material.concept == candidate.topic
    assert bundle.promotion_material.caption == candidate.caption
    assert bundle.promotion_material.hypothesis == candidate.hypothesis
    assert bundle.promotion_material.reference_ids == candidate.refs_used
    assert bundle.promotion_material.creative_direction == candidate.shooting_order
    assert bundle.promotion_material.background_intent == _inputs().background_intent
    assert bundle.reference_date == datetime(2026, 8, 26, 7, 20, tzinfo=UTC)
    assert bundle.device == _device()
    assert reviewed.status is CandidateStatus.IMAGE_AWAITING_REVIEW
    assert reviewed.agent_run_id == bundle.request_id
    assert reviewed.image_path == (
        f"generated/candidate-{candidate.candidate_id}-r{candidate.revision}/outputs/final.png"
    )


def test_candidate_image_rejects_an_unreviewed_caption_before_native_work(tmp_path: Path) -> None:
    # Given a candidate still awaiting caption review
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = store.create_candidate(
        CandidateCreate(
            workspace_id=workspace_id,
            source=CandidateSource.MANUAL,
            country="KR",
            topic="검수 전 후보",
            caption="캡션",
            hypothesis="가설",
            image_inputs=_inputs(),
        )
    )
    core = RecordingCoreRunner(tmp_path)

    # When / Then the state boundary fails before Core or Simulator work
    with pytest.raises(CandidateImageStageError):
        _ = _runner(tmp_path, store, core).generate(workspace_id, candidate.candidate_id)
    assert core.bundles == []


def test_candidate_image_preserves_review_state_when_native_environment_fails(
    tmp_path: Path,
) -> None:
    # Given the selected Mac has no usable Simulator
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _candidate(store, workspace_id)
    core = RecordingCoreRunner(tmp_path)
    resolver = FixedDeviceResolver(MarketingExecutionError("native_simulator_unavailable"))

    # When / Then the typed environment failure leaves the candidate unchanged
    with pytest.raises(CandidateImageStageError) as failure:
        _ = _runner(tmp_path, store, core, resolver).generate(workspace_id, candidate.candidate_id)
    assert "실제 Trace 캡처 환경을 준비하지 못했습니다" in failure.value.message
    assert store.get_candidate(workspace_id, candidate.candidate_id) == candidate
    assert core.bundles == []


def test_candidate_image_rejects_a_failed_or_corrupt_core_artifact(tmp_path: Path) -> None:
    # Given two approved candidates whose Core runs fail differently
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    failed_candidate = _candidate(store, workspace_id)
    corrupt_candidate = _candidate(store, workspace_id)
    failed = RecordingCoreRunner(tmp_path, state=TraceRunState.FAILED)
    corrupt = RecordingCoreRunner(tmp_path, corrupt_digest=True)

    # When / Then neither failure can advance the human review state
    with pytest.raises(CandidateImageStageError):
        _ = _runner(tmp_path, store, failed).generate(workspace_id, failed_candidate.candidate_id)
    with pytest.raises(CandidateImageStageError):
        _ = _runner(tmp_path, store, corrupt).generate(workspace_id, corrupt_candidate.candidate_id)
    assert store.get_candidate(workspace_id, failed_candidate.candidate_id) == failed_candidate
    assert store.get_candidate(workspace_id, corrupt_candidate.candidate_id) == corrupt_candidate


@dataclass(slots=True)
class RecordingFallback:
    """Stands in for the local composition, recording that it was the one that ran."""

    store: SqliteWorkspaceStore
    calls: list[CandidateId] = field(default_factory=list)

    def generate(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> CandidateRecord:
        self.calls.append(candidate_id)
        record = self.store.get_candidate(workspace_id, candidate_id)
        return self.store.attach_candidate_image(
            workspace_id,
            candidate_id,
            CandidateImageAttachment(
                path="candidates/x/r1/outputs/final.png",
                sha256="e" * 64,
                agent_run_id=f"candidate-{candidate_id}-r{record.revision}",
                expected_revision=record.revision,
                background_provenance=CandidateBackgroundProvenance(
                    query="제주 바다 노을 배경화면",
                    provider="ddgs",
                    image_url="https://cdn.example/a.jpg",
                    source_url="https://blog.example/a",
                    sha256="a" * 64,
                    pipeline=CandidateImagePipeline.LOCAL_FALLBACK,
                ),
            ),
        )


def test_no_capture_device_routes_to_the_local_composition_and_says_so(tmp_path: Path) -> None:
    # Given a host with no usable Simulator but a local composition available
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _candidate(store, workspace_id)
    core = RecordingCoreRunner(tmp_path)
    fallback = RecordingFallback(store)
    runner = CandidateImageRunner(
        store=store,
        runner=core,
        device_resolver=FixedDeviceResolver(
            MarketingExecutionError("native_simulator_unavailable")
        ),
        home=tmp_path,
        clock=lambda: datetime(2026, 8, 26, 9, 30, tzinfo=UTC),
        fallback=fallback,
    )

    # When the image stage runs
    reviewed = runner.generate(workspace_id, candidate.candidate_id)

    # Then no native work was attempted and the record says which path composed the image
    assert core.bundles == []
    assert fallback.calls == [candidate.candidate_id]
    assert reviewed.status is CandidateStatus.IMAGE_AWAITING_REVIEW
    assert reviewed.background_provenance is not None
    assert reviewed.background_provenance.pipeline is CandidateImagePipeline.LOCAL_FALLBACK


def test_the_native_path_records_the_background_the_fetcher_wrote(tmp_path: Path) -> None:
    # Given a native run whose background fetcher left its provenance artifact behind
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _candidate(store, workspace_id)
    core = RecordingCoreRunner(tmp_path)
    run_id = f"candidate-{candidate.candidate_id}-r{candidate.revision}"
    artifact = tmp_path / "generated" / run_id / "inputs" / "background-source.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    _ = artifact.write_text(
        json.dumps(
            {
                "schema_version": "trace.background-search.v1",
                "query": "제주 바다 노을 배경화면",
                "provider": "ddgs",
                "image_url": "https://cdn.example/a.jpg",
                "source_url": "https://blog.example/a",
                "artifact_sha256": "a" * 64,
                "selection": "ai_judged",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # When the image stage runs natively
    reviewed = _runner(tmp_path, store, core).generate(workspace_id, candidate.candidate_id)

    # Then the candidate carries that background, marked as the native path
    assert reviewed.background_provenance is not None
    assert reviewed.background_provenance.source_url == "https://blog.example/a"
    assert reviewed.background_provenance.pipeline is CandidateImagePipeline.NATIVE


def test_a_missing_background_artifact_is_left_absent_rather_than_invented(tmp_path: Path) -> None:
    # Given a native run that wrote no background provenance artifact
    store = SqliteWorkspaceStore(tmp_path)
    workspace_id = _workspace(store)
    candidate = _candidate(store, workspace_id)

    # When the image stage runs
    reviewed = _runner(tmp_path, store, RecordingCoreRunner(tmp_path)).generate(
        workspace_id, candidate.candidate_id
    )

    # Then nothing is recorded about a background nobody recorded
    assert reviewed.background_provenance is None

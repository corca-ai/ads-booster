"""Composing a candidate image on a host with no native capture environment.

The native path drives a real device through Appium and exports the Trace wallpaper. A
laptop with no simulator, no Appium server, and no provisioned device cannot do any of
that, and a reviewer on such a host still needs to see the candidate. This runner composes
the same three layers deterministically — the judged background, the packaged Trace
component fixture, and the packaged iPhone UI — and records that it did so.

What it cannot do is render the candidate's own schedule items and device time: the
component layer is a fixture, not a capture of that data. The run request carries them and
the provenance says `local_fallback`, so nothing here pretends to be a native export.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol

from ads_booster.candidate_generation.background_factory import persona_from_candidate
from ads_booster.candidate_generation.background_selection import (
    EXHAUSTED_CODE,
    JUDGE_FAILED_CODE,
    JUDGE_REJECTED_CODE,
)
from ads_booster.candidate_generation.errors import CandidateImageStageError
from ads_booster.contracts import (
    CaptureJob,
    CaptureScene,
    ComponentExportCanvas,
    CompositeCanvas,
    CompositeLayers,
    MarketingCompositeJob,
    TraceData,
    TraceRunRequest,
)
from ads_booster.contracts.models import (
    DeviceKind,
    DeviceTarget,
    MarketingContext,
    TraceComponent,
    TraceComponentLayout,
    TraceComponentRow,
)
from ads_booster.contracts.run import TraceRunState
from ads_booster.runtime.trace_run import TraceRunRunner
from ads_booster.runtime.trace_run_capture import LocalArtifactCapturePort, LocalComposePort
from ads_booster.runtime.trace_run_store import JsonlTraceRunStore
from ads_booster.search.image.background import BackgroundSearchError
from ads_booster.search.image.open_background import SEARCH_FAILED_CODE
from ads_booster.workspace import (
    CandidateBackgroundProvenance,
    CandidateBackgroundSubject,
    CandidateImageAttachment,
    CandidateImagePipeline,
    CandidateStatus,
)

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

    from ads_booster.candidate_generation.background_judge import JudgePersona
    from ads_booster.candidate_generation.background_selection import JudgedBackground
    from ads_booster.workspace import CandidateId, CandidateRecord, WorkspaceId

CANDIDATE_IMAGE_DIRECTORY: Final = "candidates"
_OUTPUT_IMAGE: Final = "outputs/final.png"
_BACKGROUND_IMAGE: Final = "inputs/background.png"
_BACKGROUND_PROVENANCE: Final = "inputs/background-source.json"
_SYSTEM_UI_IMAGE: Final = "inputs/iphone-ui.png"
_COMPONENT_IMAGE: Final = "work/trace-components.png"
_MISSING_INPUTS: Final = "이미지 입력값이 없는 후보입니다 — 후보를 다시 만들어 주세요."
_WRONG_STAGE: Final = (
    "캡션·주제 승인을 마친 후보만 이미지를 만들 수 있습니다 — 화면을 새로고침해 주세요."
)
_BACKGROUND_NOT_FOUND: Final = (
    "배경 이미지를 찾지 못했습니다 — 검색어를 바꾸거나 다시 시도해 주세요."
)
_BACKGROUND_JUDGE_REJECTED: Final = (
    "적합한 배경을 찾지 못했습니다 — 검색어를 조정해 다시 시도해 주세요."
)
_BACKGROUND_JUDGE_FAILED: Final = "배경 심사에 실패했습니다 — 잠시 후 다시 시도해 주세요."
_BACKGROUND_WRITE_FAILED: Final = (
    "배경 이미지를 저장하지 못했습니다 — 저장 공간과 권한을 확인해 주세요."
)
_PROVENANCE_WRITE_FAILED: Final = (
    "배경 출처 기록을 저장하지 못했습니다 — 저장 공간과 권한을 확인해 주세요."
)
_SYSTEM_UI_COPY_FAILED: Final = (
    "아이폰 UI 이미지를 준비하지 못했습니다 — 저장 공간과 권한을 확인해 주세요."
)
_COMPONENT_FIXTURE_MISSING: Final = "잠금화면 부품 이미지를 찾을 수 없습니다"
_SYSTEM_UI_MISSING: Final = "아이폰 UI 이미지를 찾을 수 없습니다"
_COMPONENT_FIXTURE_ENVIRONMENT: Final = "TRACE_AGENT_TRACE_COMPONENTS"
_SYSTEM_UI_ENVIRONMENT: Final = "TRACE_AGENT_IPHONE_UI"

# The fallback run never drives a device; the contract still requires a device target, so
# the run records this fixed placeholder instead of inventing simulator provenance.
_OFFLINE_DEVICE: Final = DeviceTarget(
    kind=DeviceKind.SIMULATOR,
    udid="00000000-0000-4000-8000-000000000000",
    platform_version="26.5",
    device_name="offline-fixture",
)

_SUBJECT_QUERIES: Final = {
    CandidateBackgroundSubject.CHARACTER_KITTY: "cute kitty character illustration",
    CandidateBackgroundSubject.CHARACTER_OTHER: "soft character illustration",
    CandidateBackgroundSubject.FAMILY_PHOTO: "warm candid family photo",
    CandidateBackgroundSubject.PERSON: "candid portrait one person",
    CandidateBackgroundSubject.PET: "candid pet photo",
    CandidateBackgroundSubject.SCENERY: "natural scenery landscape photo",
    CandidateBackgroundSubject.MINIMAL: "minimal abstract gradient texture",
    CandidateBackgroundSubject.SPORTS_TEAM: "sports team colours abstract texture",
    CandidateBackgroundSubject.NONE: "plain calm surface texture",
}
_BACKGROUND_QUERY_SUFFIX: Final = "vertical wallpaper no text no logo no phone no UI"

# Search failures that mean nothing usable came back, as opposed to a local write failure.
_SEARCH_EXHAUSTED_CODES: Final = frozenset(
    {
        "background_search_no_usable_image",
        "background_search_no_candidates",
        "background_search_invalid_image",
        "background_search_image_too_small",
        SEARCH_FAILED_CODE,
    }
)


class CandidateImageStore(Protocol):
    def get_candidate(
        self, workspace_id: WorkspaceId, candidate_id: CandidateId
    ) -> CandidateRecord: ...

    def attach_candidate_image(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
        attachment: CandidateImageAttachment,
    ) -> CandidateRecord: ...


class CandidateBackgroundPort(Protocol):
    def select(self, persona: JudgePersona, destination: Path) -> JudgedBackground: ...


class CandidateBackgroundSource(Protocol):
    def open(self) -> AbstractContextManager[CandidateBackgroundPort]: ...


@dataclass(frozen=True, slots=True)
class CandidateImageOptions:
    """Filesystem inputs and outputs for one local fallback candidate image run."""

    home: Path
    component_fixture: Path
    iphone_ui_path: Path


def build_background_query(record: CandidateRecord) -> str:
    """Return the query the open-web background search should run for this candidate.

    The generating model authors a concrete scene phrase in
    `image_inputs.background_search_query`, and that phrase is searched verbatim because it
    names what the persona actually has on their lock screen. Manual candidates that left
    the field blank, and rows written before the field existed, fall back to the mechanical
    query assembled from the subject token, the mood phrase, and the topic.
    """
    inputs = record.image_inputs
    if inputs is None:
        raise CandidateImageStageError(_MISSING_INPUTS)
    authored = inputs.background_search_query
    if authored is not None and authored.strip():
        return authored.strip()
    subject = _SUBJECT_QUERIES[inputs.background_subject]
    return (
        f"{record.country} {subject} {inputs.background_mood} {record.topic} "
        f"{_BACKGROUND_QUERY_SUFFIX}"
    )


@dataclass(frozen=True, slots=True)
class LocalCandidateImageRunner:
    """Composes one candidate lock-screen image without the native capture environment."""

    store: CandidateImageStore
    backgrounds: CandidateBackgroundSource
    options: CandidateImageOptions

    def generate(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> CandidateRecord:
        record = self.store.get_candidate(workspace_id, candidate_id)
        if record.status is not CandidateStatus.CAPTION_APPROVED:
            raise CandidateImageStageError(_WRONG_STAGE)
        query = build_background_query(record)
        fixture = self._require(
            self.options.component_fixture,
            _COMPONENT_FIXTURE_MISSING,
            _COMPONENT_FIXTURE_ENVIRONMENT,
        )
        system_ui = self._require(
            self.options.iphone_ui_path,
            _SYSTEM_UI_MISSING,
            _SYSTEM_UI_ENVIRONMENT,
        )
        job_root = (
            self.options.home / CANDIDATE_IMAGE_DIRECTORY / candidate_id / f"r{record.revision}"
        )
        self._stage_system_ui(system_ui, job_root / _SYSTEM_UI_IMAGE)
        judged = self._background(persona_from_candidate(record, query), job_root)
        background = judged.background
        run_id = f"candidate-{candidate_id}-r{record.revision}"
        result = TraceRunRunner(
            store=JsonlTraceRunStore(root=self.options.home / "state"),
            capture_port=LocalArtifactCapturePort(component_artifact=fixture),
            compose_port=LocalComposePort(),
        ).run(request=_build_request(record, candidate_id, run_id), job_root=job_root)
        if (
            result.state is not TraceRunState.COMPLETED
            or result.output_image is None
            or result.output_image_sha256 is None
        ):
            detail = result.failure.message if result.failure is not None else result.state.value
            compose_failed = f"이미지 합성에 실패했습니다 — {detail}"
            raise CandidateImageStageError(compose_failed)
        relative = (job_root / result.output_image).relative_to(self.options.home)
        return self.store.attach_candidate_image(
            workspace_id,
            candidate_id,
            CandidateImageAttachment(
                path=relative.as_posix(),
                sha256=result.output_image_sha256,
                agent_run_id=run_id,
                expected_revision=record.revision,
                background_provenance=CandidateBackgroundProvenance(
                    query=background.query,
                    provider=background.provider,
                    image_url=background.image_url,
                    source_url=background.source_url,
                    sha256=background.sha256,
                    judgment=judged.judgment,
                    pipeline=CandidateImagePipeline.LOCAL_FALLBACK,
                ),
            ),
        )

    def _background(self, persona: JudgePersona, job_root: Path) -> JudgedBackground:
        """Collect, judge, and record one background together with its provenance.

        `BackgroundSearchError` is a frozen dataclass, so it cannot carry the traceback a
        generator-based context manager assigns while unwinding. The search failure is
        therefore translated inside the selector and only the typed stage error leaves. A
        judgement that accepted nothing is its own message: the candidate stays at the
        caption-approved gate rather than receiving an image the judge rejected.
        """
        with self.backgrounds.open() as selector:
            try:
                judged = selector.select(persona, job_root / _BACKGROUND_IMAGE)
            except BackgroundSearchError as error:
                raise CandidateImageStageError(_stage_message(error)) from error
            except OSError as error:
                raise CandidateImageStageError(_BACKGROUND_WRITE_FAILED) from error
        try:
            judged.background.write_provenance(job_root / _BACKGROUND_PROVENANCE)
        except OSError as error:
            raise CandidateImageStageError(_PROVENANCE_WRITE_FAILED) from error
        return judged

    def _stage_system_ui(self, source: Path, destination: Path) -> None:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copyfile(source, destination)
        except OSError as error:
            raise CandidateImageStageError(_SYSTEM_UI_COPY_FAILED) from error

    def _require(self, path: Path, message: str, environment: str) -> Path:
        """Require one packaged asset, or the override that replaced it, to exist.

        The default ships inside the installed package, so a missing file here means the
        `environment` override points somewhere that does not exist.
        """
        if not path.is_file():
            missing = (
                f"{message} (경로: {path}) — "
                f"환경변수 {environment} 에 설정한 경로가 존재하는지 확인해 주세요."
            )
            raise CandidateImageStageError(missing)
        return path


def _stage_message(error: BackgroundSearchError) -> str:
    """Map one search or judging failure onto the message the reviewer sees.

    The exhausted-ladder failure already carries its own Korean diagnosis — which queries
    ran and what came back for them — so it is passed through rather than flattened into
    the generic "try a different query" line.
    """
    code = error.code
    if code == EXHAUSTED_CODE:
        return error.message
    if code == JUDGE_REJECTED_CODE:
        return _BACKGROUND_JUDGE_REJECTED
    if code == JUDGE_FAILED_CODE:
        return _BACKGROUND_JUDGE_FAILED
    return _BACKGROUND_NOT_FOUND if code in _SEARCH_EXHAUSTED_CODES else _BACKGROUND_WRITE_FAILED


_COMPONENT_TITLE: Final = "일정"
_ITEMS_PER_COMPONENT: Final = 4
_ROW_LAYOUTS: Final = {
    1: TraceComponentLayout.ONE_BY_ONE,
    2: TraceComponentLayout.TWO_BY_ONE,
}


def _trace_data(items: tuple[str, ...]) -> TraceData:
    """Lay the candidate's schedule out as the one row the component contract allows.

    A component holds at most four items, so one to eight schedule strings fit in one or
    two components on a single row. The layout is chosen from the component count rather
    than guessed, because the contract validates the two against each other.
    """
    components = tuple(
        TraceComponent(title=_COMPONENT_TITLE, items=items[start : start + _ITEMS_PER_COMPONENT])
        for start in range(0, len(items), _ITEMS_PER_COMPONENT)
    )
    return TraceData(
        rows=(TraceComponentRow(layout=_ROW_LAYOUTS[len(components)], components=components),)
    )


def _build_request(
    record: CandidateRecord,
    candidate_id: CandidateId,
    run_id: str,
) -> TraceRunRequest:
    inputs = record.image_inputs
    if inputs is None:
        raise CandidateImageStageError(_MISSING_INPUTS)
    context = MarketingContext(
        country=record.country,
        persona_id=f"candidate-{candidate_id}",
        promotion_material_id=f"candidate-{candidate_id}",
    )
    capture_job = CaptureJob(
        schema_version="trace.capture-job.v1",
        job_id=f"{run_id}-capture",
        context=context,
        device=_OFFLINE_DEVICE,
        scenes=(
            CaptureScene(
                scene_id=f"{run_id}-scene",
                locale=f"{inputs.language}-{record.country}",
                capture_target="trace_components",
                background_image=_BACKGROUND_IMAGE,
                component_canvas=ComponentExportCanvas(width=1206, height=2622),
                reference_date=datetime.now(UTC),
                trace_data=_trace_data(inputs.trace_items),
            ),
        ),
    )
    composite_job = MarketingCompositeJob(
        schema_version="trace.marketing-composite-job.v2",
        job_id=f"{run_id}-composite",
        context=context,
        canvas=CompositeCanvas(width=1290, height=2796),
        layers=CompositeLayers(
            background=_BACKGROUND_IMAGE,
            trace_components=_COMPONENT_IMAGE,
            iphone_ui=_SYSTEM_UI_IMAGE,
        ),
        output_image=_OUTPUT_IMAGE,
    )
    return TraceRunRequest(
        schema_version="trace.run-job.v1",
        run_id=run_id,
        idempotency_key=f"{run_id}-v1",
        capture_job=capture_job,
        composite_job=composite_job,
    )

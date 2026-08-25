from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol

from trace_capture.candidate_generation.errors import CandidateImageStageError
from trace_capture.contracts import (
    CaptureJob,
    CaptureScene,
    ComponentExportCanvas,
    CompositeCanvas,
    CompositeLayers,
    MarketingCompositeJob,
    TraceData,
    TraceRunRequest,
)
from trace_capture.contracts.models import DeviceKind, DeviceTarget, MarketingContext
from trace_capture.contracts.run import TraceRunState
from trace_capture.runtime.trace_run import TraceRunRunner
from trace_capture.runtime.trace_run_capture import LocalArtifactCapturePort, LocalComposePort
from trace_capture.runtime.trace_run_store import JsonlTraceRunStore
from trace_capture.search.image.background import BackgroundSearchError
from trace_capture.workspace import CandidateBackgroundSubject, CandidateStatus

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from pathlib import Path

    from trace_capture.search.image.background import SearchedBackground
    from trace_capture.workspace import CandidateId, CandidateRecord, WorkspaceId

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
    "배경 이미지를 찾지 못했습니다 — 네트워크 연결과 이미지 검색 도구"
    "(`ddgs` 설치 또는 `BRAVE_SEARCH_API_KEY` 설정)를 확인한 뒤 다시 시도해 주세요."
)
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

# The offline run never drives a device; the contract still requires a device target, so the
# run records this fixed placeholder instead of inventing simulator provenance.
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
        "background_search_invalid_image",
        "background_search_image_too_small",
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
        *,
        image_path: str,
        image_sha256: str,
        expected_revision: int,
    ) -> CandidateRecord: ...


class CandidateBackgroundPort(Protocol):
    def fetch(self, query: str, destination: Path) -> SearchedBackground: ...


class CandidateBackgroundSource(Protocol):
    def open(self) -> AbstractContextManager[CandidateBackgroundPort]: ...


class CandidateImageRunnerPort(Protocol):
    def generate(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> CandidateRecord: ...


@dataclass(frozen=True, slots=True)
class CandidateImageOptions:
    """Filesystem inputs and outputs for one offline candidate image run."""

    home: Path
    component_fixture: Path
    iphone_ui_path: Path


def build_background_query(record: CandidateRecord) -> str:
    """Assemble the background search query from the candidate topic and its image inputs."""
    inputs = record.image_inputs
    if inputs is None:
        raise CandidateImageStageError(_MISSING_INPUTS)
    subject = _SUBJECT_QUERIES[inputs.background_subject]
    return (
        f"{record.country} {subject} {inputs.background_mood} {record.topic} "
        f"{_BACKGROUND_QUERY_SUFFIX}"
    )


@dataclass(frozen=True, slots=True)
class CandidateImageRunner:
    """Composes one candidate lock-screen image without the native capture environment.

    The background is a provenance-verified image from the external search provider, the
    Trace component layer is the packaged offline fixture rather than a native export, and
    the deterministic local composer merges the layers. Both local layers ship inside the
    installed package, so a run does not depend on the directory the service was started
    from. Because that component layer is a fixture, the candidate's own schedule items and
    device time are recorded on the run request but are not rendered into the image;
    rendering them needs the native Appium capture path.
    """

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
        self._background(query, job_root)
        result = TraceRunRunner(
            store=JsonlTraceRunStore(root=self.options.home / "state"),
            capture_port=LocalArtifactCapturePort(component_artifact=fixture),
            compose_port=LocalComposePort(),
        ).run(request=_build_request(record, candidate_id), job_root=job_root)
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
            image_path=relative.as_posix(),
            image_sha256=result.output_image_sha256,
            expected_revision=record.revision,
        )

    def _background(self, query: str, job_root: Path) -> None:
        """Search, verify, and record one background together with its provenance.

        `BackgroundSearchError` is a frozen dataclass, so it cannot carry the traceback a
        generator-based context manager assigns while unwinding. The search failure is
        therefore translated inside the open fetcher and only the typed stage error leaves.
        """
        with self.backgrounds.open() as fetcher:
            try:
                background = fetcher.fetch(query, job_root / _BACKGROUND_IMAGE)
            except BackgroundSearchError as error:
                message = (
                    _BACKGROUND_NOT_FOUND
                    if error.code in _SEARCH_EXHAUSTED_CODES
                    else _BACKGROUND_WRITE_FAILED
                )
                raise CandidateImageStageError(message) from error
            except OSError as error:
                raise CandidateImageStageError(_BACKGROUND_WRITE_FAILED) from error
        try:
            background.write_provenance(job_root / _BACKGROUND_PROVENANCE)
        except OSError as error:
            raise CandidateImageStageError(_PROVENANCE_WRITE_FAILED) from error

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


def _build_request(record: CandidateRecord, candidate_id: CandidateId) -> TraceRunRequest:
    inputs = record.image_inputs
    if inputs is None:
        raise CandidateImageStageError(_MISSING_INPUTS)
    run_id = f"candidate-{candidate_id}-r{record.revision}"
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
                trace_data=TraceData(items=inputs.trace_items),
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

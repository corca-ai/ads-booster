from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, ClassVar, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ads_booster.candidate_generation.errors import CandidateImageStageError
from ads_booster.contracts.generation import (
    MarketingContextBundle,
    PersonaProfile,
    PromotionMaterial,
)
from ads_booster.contracts.run import TraceRunState
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.workspace import (
    CandidateBackgroundJudgment,
    CandidateBackgroundProvenance,
    CandidateImageAttachment,
    CandidateImagePipeline,
    CandidateStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.automation import GenerateOnePort
    from ads_booster.contracts.models import DeviceTarget
    from ads_booster.workspace import CandidateId, CandidateRecord, WorkspaceId

_MISSING_INPUTS: Final = "이미지 입력값이 없는 후보입니다 — 후보를 다시 만들어 주세요."
_WRONG_STAGE: Final = (
    "캡션·주제 승인을 마친 후보만 이미지를 만들 수 있습니다 — 화면을 새로고침해 주세요."
)
_NATIVE_ENVIRONMENT_FAILED: Final = "실제 Trace 캡처 환경을 준비하지 못했습니다"
_NATIVE_RUN_FAILED: Final = "Agent가 검증된 Trace 이미지를 만들지 못했습니다"
_ARTIFACT_INVALID: Final = "Agent의 이미지 산출물을 검증하지 못했습니다"
_NO_FALLBACK: Final = (
    "실제 Trace 캡처 환경이 없고 대체 합성 경로도 설정되지 않았습니다 — 관리자에게 문의하세요."
)
_BACKGROUND_PROVENANCE: Final = "inputs/background-source.json"


class _BackgroundArtifact(BaseModel):
    """The `background-source.json` the fetcher writes, as the image stage reads it back."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    query: str = Field(min_length=1, max_length=1_000)
    provider: str = Field(min_length=1, max_length=64)
    image_url: str = Field(min_length=1, max_length=4_096)
    source_url: str = Field(min_length=1, max_length=4_096)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    judgment: CandidateBackgroundJudgment | None = None


_BACKGROUND_ARTIFACT: TypeAdapter[_BackgroundArtifact] = TypeAdapter(_BackgroundArtifact)


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


class DeviceResolver(Protocol):
    def resolve(self) -> DeviceTarget: ...


class CandidateImageRunnerPort(Protocol):
    def generate(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> CandidateRecord: ...


@dataclass(frozen=True, slots=True)
class CandidateImageRunner:
    """Runs the native Trace capture, or the local composition when there is no device.

    Which one ran is decided by whether a capture device resolves, and it is recorded on
    the candidate rather than inferred: the native path renders the candidate's own
    schedule and clock through Appium, the fallback merges a packaged component fixture and
    cannot. A reviewer looking at an image has to be able to tell those apart.
    """

    store: CandidateImageStore
    runner: GenerateOnePort
    device_resolver: DeviceResolver
    home: Path
    clock: Callable[[], datetime]
    fallback: CandidateImageRunnerPort | None = None

    def generate(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> CandidateRecord:
        record = self.store.get_candidate(workspace_id, candidate_id)
        if record.status is not CandidateStatus.CAPTION_APPROVED:
            raise CandidateImageStageError(_WRONG_STAGE)
        try:
            device = self.device_resolver.resolve()
        except MarketingExecutionError as error:
            if self.fallback is None:
                message = f"{_NATIVE_ENVIRONMENT_FAILED} — {error}"
                raise CandidateImageStageError(message) from error
            return self.fallback.generate(workspace_id, candidate_id)
        bundle = _candidate_bundle(record, device, self.clock())
        result = self.runner.run(bundle)
        if result.state is not TraceRunState.COMPLETED or result.output_image is None:
            detail = result.failure.message if result.failure is not None else result.state.value
            message = f"{_NATIVE_RUN_FAILED} — {detail}"
            raise CandidateImageStageError(message)
        if result.output_image_sha256 is None:
            raise CandidateImageStageError(_ARTIFACT_INVALID)
        generated_root = (self.home / "generated" / bundle.request_id).resolve()
        output = (generated_root / result.output_image).resolve()
        if not output.is_relative_to(generated_root) or not output.is_file():
            raise CandidateImageStageError(_ARTIFACT_INVALID)
        try:
            digest = sha256(output.read_bytes()).hexdigest()
        except OSError as error:
            raise CandidateImageStageError(_ARTIFACT_INVALID) from error
        if digest != result.output_image_sha256:
            raise CandidateImageStageError(_ARTIFACT_INVALID)
        return self.store.attach_candidate_image(
            workspace_id,
            candidate_id,
            CandidateImageAttachment(
                path=output.relative_to(self.home.resolve()).as_posix(),
                sha256=digest,
                agent_run_id=bundle.request_id,
                expected_revision=record.revision,
                background_provenance=_background_provenance(generated_root),
            ),
        )


def _background_provenance(job_root: Path) -> CandidateBackgroundProvenance | None:
    """Read back what the background fetcher recorded for this run, if it recorded any.

    The artifact is written by whichever fetcher the seam was given, so this is the one
    place both fetchers meet. A run whose fetcher wrote nothing readable simply carries no
    background provenance; it does not get an invented one.
    """
    try:
        payload = (job_root / _BACKGROUND_PROVENANCE).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        artifact = _BACKGROUND_ARTIFACT.validate_json(payload)
    except ValidationError:
        return None
    return CandidateBackgroundProvenance(
        query=artifact.query,
        provider=artifact.provider,
        image_url=artifact.image_url,
        source_url=artifact.source_url,
        sha256=artifact.artifact_sha256,
        judgment=artifact.judgment,
        pipeline=CandidateImagePipeline.NATIVE,
    )


def _candidate_bundle(
    record: CandidateRecord,
    device: DeviceTarget,
    now: datetime,
) -> MarketingContextBundle:
    inputs = record.image_inputs
    if inputs is None:
        raise CandidateImageStageError(_MISSING_INPUTS)
    hour, minute = (int(part) for part in inputs.device_time.split(":"))
    reference_date = now.astimezone(UTC).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    identifier = f"candidate-{record.candidate_id}"
    return MarketingContextBundle(
        schema_version="trace.marketing-context.v1",
        request_id=record.agent_run_id or f"{identifier}-r{record.revision}",
        persona=PersonaProfile(
            persona_id=identifier,
            country=record.country,
            locale=f"{inputs.language}-{record.country}",
        ),
        promotion_material=PromotionMaterial(
            promotion_material_id=identifier,
            concept=record.topic[:120],
            caption=record.caption,
            hypothesis=record.hypothesis,
            reference_ids=record.refs_used,
            creative_direction=record.shooting_order or None,
            # The authored search query is the most concrete statement of what this
            # persona keeps on their lock screen, so it is what the planner is given.
            background_intent=inputs.background_search_query or inputs.background_intent,
            trace_items=inputs.trace_items,
        ),
        reference_date=reference_date,
        device=device,
    )

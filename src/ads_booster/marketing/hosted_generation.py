"""Planless, structured Codex turns for hosted candidate generation."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ads_booster.marketing.inbox import ExecutionAdmission, MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult, TaskStatus
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.marketing.native_capture import (
        HostedWorkspaceCaptureExecutor,
        PreparedCodexAppiumJob,
    )

PIPELINE: Final = "hosted_workspace_generation_v1"
_DEFAULT_TIMEOUT_SECONDS: Final = 180.0
_WORKSPACE_DIRECTORY: Final = "codex-generation"
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class GenerationModel(BaseModel):
    """Strict input and output records exchanged with the Codex process."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class GenerationPersonaTaste(GenerationModel):
    background_subject: Annotated[str, Field(min_length=1, max_length=40)]
    background_mood: Annotated[str, Field(min_length=1, max_length=40)]
    font: Annotated[str, Field(min_length=1, max_length=40)]


class GenerationPersona(GenerationModel):
    display_name: Annotated[str, Field(min_length=1, max_length=120)]
    age: Annotated[int, Field(ge=0, le=150)]
    region: Annotated[str, Field(min_length=1, max_length=160)]
    occupation: Annotated[str, Field(min_length=1, max_length=160)]
    concept: Annotated[str, Field(min_length=1, max_length=500)]
    domain: Annotated[str, Field(min_length=1, max_length=40)]
    interests: tuple[Annotated[str, Field(min_length=1, max_length=80)], ...] = Field(max_length=8)
    life_rhythm: Annotated[str, Field(min_length=1, max_length=500)]
    taste: GenerationPersonaTaste


class HostedGenerationRequest(GenerationModel):
    pipeline: Literal["hosted_workspace_generation_v1"]
    persona_id: Annotated[str | None, Field(max_length=128)] = None
    persona: GenerationPersona | None = None
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")]
    count: Annotated[int, Field(ge=1, le=8)] = 4
    context_profile_id: Annotated[str | None, Field(max_length=128)] = None
    requested_by: Literal["hosted_workspace"]


class GeneratedImageInputs(GenerationModel):
    trace_items: tuple[
        Annotated[
            str,
            Field(
                min_length=7,
                max_length=80,
                pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d\s+.+$",
            ),
        ],
        ...,
    ] = Field(
        min_length=5,
        max_length=8,
    )
    device_time: Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]
    background_subject: Literal[
        "scenery",
        "character_kitty",
        "character_other",
        "family_photo",
        "person",
        "pet",
        "minimal",
        "sports_team",
        "none",
    ]
    background_mood: Annotated[str, Field(min_length=1, max_length=40)]
    background_search_query: Annotated[str | None, Field(max_length=200)] = None
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")]


class GeneratedCandidate(GenerationModel):
    topic: Annotated[str, Field(min_length=1, max_length=200)]
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    caption: Annotated[str, Field(min_length=1, max_length=10_000)]
    hypothesis: Annotated[str, Field(min_length=1, max_length=2_000)]
    posting_slot: Literal["morning", "evening", "manual"]
    persona_domain: Annotated[str | None, Field(max_length=40)] = None
    refs_used: tuple[Annotated[str, Field(min_length=1, max_length=120)], ...] = Field(
        max_length=16
    )
    principles_applied: tuple[Annotated[int, Field(ge=1)], ...] = Field(min_length=1, max_length=16)
    appium_prompt: Annotated[str, Field(max_length=10_000)] = ""
    image_inputs: GeneratedImageInputs


class HostedGenerationResponse(GenerationModel):
    candidates: tuple[GeneratedCandidate, ...] = Field(max_length=8)


class StructuredCodexGeneration(Protocol):
    """The small Codex capability required by a hosted generation task."""

    def run_generation_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class PreparedHostedGeneration:
    request: HostedGenerationRequest
    execution_admission: ExecutionAdmission
    prompt: str
    schema: JsonObject
    workspace: Path


@dataclass(frozen=True, slots=True)
class HostedWorkspaceGenerationExecutor:
    """Prepare and execute one brokered generation turn without a plan object."""

    codex: StructuredCodexGeneration
    output_root: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def prepare(self, task: MarketingTask) -> PreparedHostedGeneration:
        match task.kind:
            case TaskKind.GENERATE_CANDIDATES:
                pass
            case _:
                raise MarketingExecutionError("unsupported_hosted_generation_task")
        try:
            request = HostedGenerationRequest.model_validate(task.payload)
        except ValidationError as error:
            pipeline = task.payload.get("pipeline")
            if pipeline != PIPELINE:
                raise MarketingExecutionError("unsupported_hosted_generation_pipeline") from error
            raise MarketingExecutionError("hosted_generation_payload_invalid") from error
        workspace, admission = self._prepare_workspace(task)
        return PreparedHostedGeneration(
            request=request,
            execution_admission=admission,
            prompt=_generation_prompt(request),
            schema=_generation_schema(),
            workspace=workspace,
        )

    def execute(self, prepared: PreparedHostedGeneration) -> TaskResult:
        try:
            raw_result = self.codex.run_generation_job(
                prepared.prompt,
                prepared.schema,
                workspace=prepared.workspace,
                timeout_seconds=self.timeout_seconds,
            )
        except (OSError, RuntimeError) as error:
            raise MarketingExecutionError(
                "hosted_generation_codex_failed",
                unknown_side_effect=True,
            ) from error
        try:
            generated = HostedGenerationResponse.model_validate(raw_result)
        except ValidationError as error:
            raise MarketingExecutionError(
                "hosted_generation_result_invalid",
                unknown_side_effect=True,
            ) from error
        if not _candidates_match_request(generated, prepared.request):
            raise MarketingExecutionError(
                "hosted_generation_result_invalid",
                unknown_side_effect=True,
            )
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "pipeline": PIPELINE,
                "persona_id": prepared.request.persona_id,
                "requested": prepared.request.count,
                "failures": prepared.request.count - len(generated.candidates),
                "candidates": [
                    _candidate_output(candidate, prepared.prompt)
                    for candidate in generated.candidates
                ],
            },
        )

    def _prepare_workspace(
        self,
        task: MarketingTask,
    ) -> tuple[Path, ExecutionAdmission]:
        request_digest = sha256(task.model_dump_json().encode()).hexdigest()
        root = self.output_root.resolve()
        workspace = (root / _WORKSPACE_DIRECTORY / request_digest).resolve()
        if not workspace.is_relative_to(root):
            raise MarketingExecutionError("hosted_generation_workspace_invalid")
        try:
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            workspace.chmod(0o700)
        except OSError as error:
            raise MarketingExecutionError("hosted_generation_workspace_unavailable") from error
        return (
            workspace,
            ExecutionAdmission(
                job_digest=request_digest,
                export_nonce=secrets.token_hex(32),
                workspace_id=f"codex-generation:{request_digest}",
            ),
        )


@dataclass(frozen=True, slots=True)
class PlanlessHostedTaskExecutor:
    capture: HostedWorkspaceCaptureExecutor
    generation: HostedWorkspaceGenerationExecutor

    def prepare(self, task: MarketingTask) -> PreparedCodexAppiumJob | PreparedHostedGeneration:
        match task.kind:
            case TaskKind.CAPTURE:
                return self.capture.prepare(task)
            case TaskKind.GENERATE_CANDIDATES:
                return self.generation.prepare(task)
            case _:
                raise MarketingExecutionError("unsupported_hosted_task")

    def execute(self, prepared: PreparedCodexAppiumJob | PreparedHostedGeneration) -> TaskResult:
        if isinstance(prepared, PreparedHostedGeneration):
            return self.generation.execute(prepared)
        return self.capture.execute(prepared)


def _generation_prompt(request: HostedGenerationRequest) -> str:
    return (
        "Generate distinct, truthful Trace marketing candidates from this hosted request. "
        "Every image_inputs.trace_items item must be a real schedule in the exact HH:MM title "
        "format, with five to seven items. Return only the schema-conforming JSON output. "
        "Do not invent references or provenance.\n"
        f"<hosted-generation-request>{request.model_dump_json()}</hosted-generation-request>"
    )


def _generation_schema() -> JsonObject:
    schema: JsonObject = _JSON_OBJECT.validate_python(HostedGenerationResponse.model_json_schema())
    return schema


def _candidates_match_request(
    generated: HostedGenerationResponse,
    request: HostedGenerationRequest,
) -> bool:
    if len(generated.candidates) > request.count:
        return False
    return all(
        candidate.country == request.country and candidate.image_inputs.language == request.language
        for candidate in generated.candidates
    )


def _candidate_output(candidate: GeneratedCandidate, prompt: str) -> JsonObject:
    image_inputs = candidate.image_inputs
    return {
        "topic": candidate.topic,
        "country": candidate.country,
        "caption": candidate.caption,
        "hypothesis": candidate.hypothesis,
        "posting_slot": candidate.posting_slot,
        "persona_domain": candidate.persona_domain,
        "refs_used": list(candidate.refs_used),
        "principles_applied": list(candidate.principles_applied),
        "appium_prompt": candidate.appium_prompt,
        "image_inputs": {
            "trace_items": list(image_inputs.trace_items),
            "device_time": image_inputs.device_time,
            "background_subject": image_inputs.background_subject,
            "background_mood": image_inputs.background_mood,
            "background_search_query": image_inputs.background_search_query,
            "language": image_inputs.language,
        },
        "provenance": {
            "documents": [],
            "model": "codex_cli",
            "instruction_chars": len(prompt),
            "generated_at": datetime.now(UTC).timestamp(),
            "assigned_domains": (
                [] if candidate.persona_domain is None else [candidate.persona_domain]
            ),
            "reference_ids": list(candidate.refs_used),
            "caption_form": "codex_structured",
        },
    }

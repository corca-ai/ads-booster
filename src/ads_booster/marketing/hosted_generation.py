"""Planless, structured Codex turns for hosted candidate generation.

The Codex execution path is the one from #60: one structured `codex exec` turn per call,
schema-constrained output, and an execution admission recorded before the process starts.
What sits on top of it is the marketing content engine — the context corpus, the reference
sample and the rules the corpus settled — because a Codex turn that reads nothing produces
a caption about nothing in particular, which is what the 540-character prompt it replaces
was doing.
"""

from __future__ import annotations

import random
import re
import secrets
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from ads_booster.candidate_generation import (
    DEFAULT_MAX_BATCH,
    REQUIRED_DOCUMENTS,
    CandidateContextSource,
    CandidateDraftEngine,
    CandidateFormatError,
    CandidateGenerationError,
    CandidateReferenceSource,
    assign_domains,
    default_context_directory,
    default_domain_shuffle,
)
from ads_booster.contracts.feedback import FeedbackContext, feedback_context_sha256
from ads_booster.marketing.hosted_candidate_judgment import (
    HostedCandidateJudgmentExecutor,
    PreparedCandidateJudgment,
)
from ads_booster.marketing.hosted_creative_judgment import (
    HostedCreativeJudgmentExecutor,
    PreparedCreativeJudgment,
)
from ads_booster.marketing.hosted_experiment_evaluation import (
    HostedExperimentEvaluationExecutor,
    PreparedExperimentEvaluation,
)
from ads_booster.marketing.hosted_judgment import (
    HostedMarketingJudgmentExecutor,
    PreparedMarketingJudgment,
)
from ads_booster.marketing.hosted_learning_judgment import (
    HostedLearningJudgmentExecutor,
    PreparedLearningJudgment,
)
from ads_booster.marketing.hosted_reassessment_judgment import (
    HostedOutcomeReassessmentExecutor,
    PreparedOutcomeReassessment,
)
from ads_booster.marketing.hosted_reference_research import (
    HostedReferenceResearchExecutor,
    PreparedReferenceResearch,
)
from ads_booster.marketing.inbox import ExecutionAdmission, MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult, TaskStatus

# Imported at runtime: pydantic resolves the "before" validator's annotation while it
# builds the model, so a type-checking-only import would fail there.
from ads_booster.transport.json_types import (
    JsonObject,
    JsonValue,
)
from ads_booster.workspace import (
    CandidateAccountBrief,
    CandidateBackgroundSubject,
    CandidateEventColor,
    CandidateHistoryEntry,
    CandidatePersonaDomain,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ads_booster.candidate_generation import (
        CandidateContextBundle,
        CandidateDocument,
        DraftedCandidate,
        ReferencePool,
    )
    from ads_booster.marketing.native_capture import (
        HostedWorkspaceCaptureExecutor,
        PreparedCodexAppiumJob,
    )

PIPELINE: Final = "hosted_workspace_generation_v1"
_DEFAULT_TIMEOUT_SECONDS: Final = 180.0
_WORKSPACE_DIRECTORY: Final = "codex-generation"
_CODEX_MODEL: Final = "codex_cli"
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
    # What this persona has already been given, newest first, so a batch does not restate
    # last week's. Absent on a control plane that predates the field, which is why it
    # defaults to empty rather than being required: an older publisher still gets captions,
    # it just gets them without this guard.
    recent_topics: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=200)], ...],
        Field(max_length=32),
    ] = ()
    feedback_context: FeedbackContext | None = None
    feedback_context_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    requested_by: Literal["hosted_workspace"]


_LEGACY_CLOCK: Final = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
# The lock screen shows one week, so a row sits on one of seven days and a bar spans at
# most the rest of them.
_WEEK_DAYS: Final = 7


class GeneratedScheduleEntry(GenerationModel):
    """One row of the week, as the generating model writes it.

    Every field is required and the optional ones are nullable rather than defaulted: a
    strict structured-output schema drops properties it may omit, and a row that arrives
    without a day would silently pile onto the captured day with the rest.
    """

    title: Annotated[str, Field(min_length=1, max_length=40)]
    day: Annotated[int, Field(ge=0, le=6)]
    days: Annotated[int, Field(ge=1, le=7)]
    time: Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")] | None
    color: CandidateEventColor | None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_string(cls, value: JsonValue) -> JsonValue:
        """Read the old `"HH:MM 제목"` row rather than losing the batch it came with.

        The schema asks for objects, so a string here means the model answered in the shape
        it was not given. That is a reason to place the row on the captured day, not a
        reason to throw away three captions that arrived beside it.
        """
        if not isinstance(value, str):
            return value
        head, separator, tail = value.partition(" ")
        timed = separator and _LEGACY_CLOCK.fullmatch(head) and tail.strip()
        title = tail.strip() if timed else value.strip()
        return {
            "title": title,
            "day": 0,
            "days": 1,
            "time": head if timed else None,
            "color": None,
        }

    @model_validator(mode="after")
    def fit_the_span_inside_the_week(self) -> GeneratedScheduleEntry:
        """Shorten a bar that runs off the end of the week instead of losing the turn.

        `day + days > 7` is rejected downstream by both `TraceScheduleItem` and
        `CandidateScheduleEntry`, and this model validates a whole Codex turn at once — so
        one over-long bar written here would throw away up to eight captions the turn
        already paid for. The instruction states the bound, and a model that misses it is
        off by a day or two on one row, which is a row to trim rather than a batch to drop.
        """
        overflow = self.day + self.days - _WEEK_DAYS
        if overflow <= 0:
            return self
        return self.model_copy(update={"days": self.days - overflow})


class GeneratedImageInputs(GenerationModel):
    trace_items: tuple[GeneratedScheduleEntry, ...] = Field(
        min_length=5,
        max_length=24,
    )
    # Required for the same reason as the query below: a defaulted property is one a strict
    # schema may drop, and the to-dos are the entire right-hand panel of the screen. Nothing
    # upstream authors them, so a dropped key renders that cell empty. Required means the
    # key must be written, not that it must be long - an empty list still validates, so no
    # batch dies here over a list the instruction already asks for.
    trace_todos: tuple[Annotated[str, Field(min_length=1, max_length=60)], ...] = Field(
        max_length=20,
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
    # The query comes before the mood on purpose. Written the other way round, the model
    # produced the mood first and then reworded it into the query - three stored rows in a
    # row did exactly that, down to "해질녘 캠핑장, 아이 둘의 뒷모습" becoming "해질녘
    # 캠핑장 아이 둘 뒷모습 가족사진 배경화면". Naming the wallpaper first gives the mood
    # something to describe, instead of giving the query something to paraphrase.
    #
    # Required and nullable rather than defaulted, for the reason `GeneratedScheduleEntry`
    # spells out: a strict structured-output schema drops a property it is allowed to omit,
    # and this one was being dropped. A batch that never writes the field falls back to
    # `background_intent`, which is composed mechanically as "<subject>: <mood>" - so the
    # image search ran "sports_team: 밤 경기 외야석 너머 환한 전광판", an English token
    # followed by the scene sentence rule 8 exists to forbid. Ordering the fields cannot
    # help when the field is not asked for at all.
    background_search_query: Annotated[str | None, Field(max_length=200)]
    background_mood: Annotated[str, Field(min_length=1, max_length=40)]
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
    """One admitted generation batch, with everything decided before any process starts.

    The context bundle and the reference pool are read here rather than mid-batch: a corpus
    that cannot be read is an ordinary failed task, and finding that out after the first
    Codex turn has already run would make it an unknown side effect instead.
    """

    request: HostedGenerationRequest
    execution_admission: ExecutionAdmission
    schema: JsonObject
    workspace: Path
    bundle: CandidateContextBundle
    pool: ReferencePool
    domains: tuple[CandidatePersonaDomain, ...]
    brief: CandidateAccountBrief | None


@dataclass(frozen=True, slots=True)
class _CodexDraftClient:
    """The engine's provider seam, spent as one structured Codex turn per candidate.

    Each turn gets its own directory below the batch workspace because `run_generation_job`
    records an invocation receipt and refuses a second run in the same place — which is the
    whole point of that receipt, and the reason a batch cannot be one workspace.
    """

    codex: StructuredCodexGeneration
    schema: JsonObject
    workspace: Path
    timeout_seconds: float

    def draft(self, instruction: str, *, call_id: str) -> JsonValue:
        workspace = self.workspace / f"call-{call_id}"
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace.chmod(0o700)
        return self.codex.run_generation_job(
            instruction,
            self.schema,
            workspace=workspace,
            timeout_seconds=self.timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class HostedWorkspaceGenerationExecutor:
    """Prepare and execute one brokered generation batch without a plan object."""

    codex: StructuredCodexGeneration
    output_root: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    # Where the marketing corpus is read from. Left unset it resolves the operator override,
    # then the working directory, then the copy packaged into the wheel — which is what a
    # released Mac worker actually has.
    context_directory: Path | None = None
    # Injected in tests so a reference sample is predictable; production draws at random.
    sample_references: Callable[[Sequence[CandidateDocument], int], Sequence[CandidateDocument]] = (
        random.sample
    )
    # Breaks the domain order for an account-less batch; injected so a test can name it.
    shuffle: Callable[[Sequence[CandidatePersonaDomain]], Sequence[CandidatePersonaDomain]] = (
        default_domain_shuffle
    )
    # How many candidates one Codex turn is asked for. A request larger than this is
    # written as several turns in order, each shown what the earlier ones wrote.
    max_batch: int = DEFAULT_MAX_BATCH

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
        if (request.feedback_context is None) != (request.feedback_context_sha256 is None):
            raise MarketingExecutionError("hosted_generation_feedback_context_invalid")
        if request.feedback_context is not None and (
            request.feedback_context.stage != "caption"
            or request.feedback_context.scope.account_id != task.account_id
            or request.feedback_context.scope.context_profile_id != request.context_profile_id
            or request.feedback_context.immediate_correction is not None
            or feedback_context_sha256(request.feedback_context) != request.feedback_context_sha256
        ):
            raise MarketingExecutionError("hosted_generation_feedback_context_invalid")
        brief = None if request.persona is None else _account_brief(request.persona)
        directory = (
            default_context_directory(Path.cwd())
            if self.context_directory is None
            else self.context_directory
        )
        try:
            bundle = CandidateContextSource(directory, required=REQUIRED_DOCUMENTS).load()
            pool = CandidateReferenceSource(directory).load(request.country)
        except CandidateGenerationError as error:
            raise MarketingExecutionError("hosted_generation_context_unavailable") from error
        workspace, admission = self._prepare_workspace(task)
        return PreparedHostedGeneration(
            request=request,
            execution_admission=admission,
            schema=_generation_schema(),
            workspace=workspace,
            bundle=bundle,
            pool=pool,
            domains=(
                assign_domains(request.count, self.shuffle)
                if brief is None
                else (brief.domain,) * request.count
            ),
            brief=brief,
        )

    def execute(self, prepared: PreparedHostedGeneration) -> TaskResult:
        persona = prepared.request.persona
        engine = CandidateDraftEngine(
            client=_CodexDraftClient(
                codex=self.codex,
                schema=prepared.schema,
                workspace=prepared.workspace,
                timeout_seconds=self.timeout_seconds,
            ),
            model=_CODEX_MODEL,
            sample_references=self.sample_references,
            max_batch=self.max_batch,
        )
        try:
            batch = engine.draft(
                bundle=prepared.bundle,
                pool=prepared.pool,
                country=prepared.request.country,
                language=prepared.request.language,
                domains=prepared.domains,
                brief=prepared.brief,
                interests=() if persona is None else persona.interests,
                history=_recent_history(prepared.request.recent_topics),
                learned_feedback=tuple(
                    rule.instruction
                    for rule in (
                        prepared.request.feedback_context.rules
                        if prepared.request.feedback_context
                        else ()
                    )
                    if rule.stage == "caption" and "candidate_generation" in rule.targets
                ),
            )
        except CandidateFormatError as error:
            raise MarketingExecutionError(
                "hosted_generation_result_invalid",
                unknown_side_effect=True,
            ) from error
        except CandidateGenerationError as error:
            raise MarketingExecutionError(
                "hosted_generation_codex_failed",
                unknown_side_effect=True,
            ) from error
        generated = HostedGenerationResponse(
            candidates=tuple(_generated_candidate(drafted) for drafted in batch.drafts)
        )
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
                "failures": batch.failures,
                "failure_reason": batch.failure_reason,
                "feedback_application_sha256": prepared.request.feedback_context_sha256,
                "candidates": [
                    _candidate_output(candidate, drafted)
                    for candidate, drafted in zip(generated.candidates, batch.drafts, strict=True)
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


type PlanlessPrepared = (
    PreparedCodexAppiumJob
    | PreparedHostedGeneration
    | PreparedMarketingJudgment
    | PreparedCreativeJudgment
    | PreparedCandidateJudgment
    | PreparedExperimentEvaluation
    | PreparedLearningJudgment
    | PreparedReferenceResearch
    | PreparedOutcomeReassessment
)


@dataclass(frozen=True, slots=True)
class PlanlessHostedTaskExecutor:
    capture: HostedWorkspaceCaptureExecutor
    generation: HostedWorkspaceGenerationExecutor
    judgment: HostedMarketingJudgmentExecutor
    creative_judgment: HostedCreativeJudgmentExecutor
    candidate_judgment: HostedCandidateJudgmentExecutor
    experiment_evaluation: HostedExperimentEvaluationExecutor
    learning_judgment: HostedLearningJudgmentExecutor
    reference_research: HostedReferenceResearchExecutor
    outcome_reassessment: HostedOutcomeReassessmentExecutor

    def prepare(
        self,
        task: MarketingTask,
    ) -> PlanlessPrepared:
        match task.kind:
            case TaskKind.CAPTURE:
                return self.capture.prepare(task)
            case TaskKind.GENERATE_CANDIDATES:
                return self.generation.prepare(task)
            case TaskKind.MARKETING_JUDGMENT:
                return self._prepare_marketing_judgment(task)
            case _:
                raise MarketingExecutionError("unsupported_hosted_task")

    def execute(
        self,
        prepared: PlanlessPrepared,
    ) -> TaskResult:
        if isinstance(prepared, PreparedHostedGeneration):
            return self.generation.execute(prepared)
        if isinstance(
            prepared,
            (
                PreparedMarketingJudgment,
                PreparedCreativeJudgment,
                PreparedCandidateJudgment,
                PreparedExperimentEvaluation,
                PreparedLearningJudgment,
                PreparedReferenceResearch,
                PreparedOutcomeReassessment,
            ),
        ):
            return self._execute_marketing_judgment(prepared)
        return self.capture.execute(prepared)

    def _prepare_marketing_judgment(
        self,
        task: MarketingTask,
    ) -> (
        PreparedMarketingJudgment
        | PreparedCreativeJudgment
        | PreparedCandidateJudgment
        | PreparedExperimentEvaluation
        | PreparedLearningJudgment
        | PreparedReferenceResearch
        | PreparedOutcomeReassessment
    ):
        judgment = task.payload.get("judgment")
        if judgment == "market_research":
            prepared = self.reference_research.prepare(task)
        elif judgment == "creative_plan":
            prepared = self.creative_judgment.prepare(task)
        elif judgment == "candidate_materialization":
            prepared = self.candidate_judgment.prepare(task)
        elif judgment == "experiment_evaluation":
            prepared = self.experiment_evaluation.prepare(task)
        elif judgment == "learning_synthesis":
            prepared = self.learning_judgment.prepare(task)
        elif judgment == "outcome_reassessment":
            prepared = self.outcome_reassessment.prepare(task)
        else:
            prepared = self.judgment.prepare(task)
        return prepared

    def _execute_marketing_judgment(
        self,
        prepared: (
            PreparedMarketingJudgment
            | PreparedCreativeJudgment
            | PreparedCandidateJudgment
            | PreparedExperimentEvaluation
            | PreparedLearningJudgment
            | PreparedReferenceResearch
            | PreparedOutcomeReassessment
        ),
    ) -> TaskResult:
        if isinstance(prepared, PreparedMarketingJudgment):
            result = self.judgment.execute(prepared)
        elif isinstance(prepared, PreparedCreativeJudgment):
            result = self.creative_judgment.execute(prepared)
        elif isinstance(prepared, PreparedCandidateJudgment):
            result = self.candidate_judgment.execute(prepared)
        elif isinstance(prepared, PreparedExperimentEvaluation):
            result = self.experiment_evaluation.execute(prepared)
        elif isinstance(prepared, PreparedReferenceResearch):
            result = self.reference_research.execute(prepared)
        elif isinstance(prepared, PreparedOutcomeReassessment):
            result = self.outcome_reassessment.execute(prepared)
        else:
            result = self.learning_judgment.execute(prepared)
        return result


def _generation_schema() -> JsonObject:
    """The output schema one Codex turn is held to.

    This stays the hosted callback's own response model rather than a schema derived from
    the draft type. The two describe the same candidate, and generating straight into the
    delivery shape removes the failure mode where a caption the batch already paid for is
    thrown away by a mapping mismatch.
    """
    schema: JsonObject = _JSON_OBJECT.validate_python(HostedGenerationResponse.model_json_schema())
    return schema


def _recent_history(topics: Sequence[str]) -> tuple[CandidateHistoryEntry, ...]:
    """Turn the control plane's recent topics into the history block the prompt shows.

    No domain is carried across: the control plane stores the topic, and inventing a domain
    to fill the field would put a fact in the prompt that nobody observed. The block renders
    those as "도메인 미기록", which is what they are.
    """
    return tuple(CandidateHistoryEntry(persona_domain=None, topic=topic) for topic in topics)


def _account_brief(persona: GenerationPersona) -> CandidateAccountBrief:
    """Describe the requested persona in the terms the instruction states.

    The two shapes already agree field for field; what differs is that the request carries
    the domain and the background subject as free text while the instruction needs closed
    vocabulary. An unusable domain fails the task, because the domain is what the whole
    batch's identity is pinned to and quietly relabelling it is the exact drift this path
    exists to stop. An unusable background subject degrades to `none`, which is a real
    member of that vocabulary meaning "nothing was recorded".
    """
    domain = _persona_domain(persona.domain)
    if domain is None:
        raise MarketingExecutionError("hosted_generation_persona_domain_unknown")
    return CandidateAccountBrief(
        display_name=persona.display_name,
        age=persona.age,
        region=persona.region,
        occupation=persona.occupation,
        concept=persona.concept,
        domain=domain,
        interests=persona.interests,
        life_rhythm=persona.life_rhythm,
        background_subject=_background_subject(persona.taste.background_subject),
        background_mood=persona.taste.background_mood,
    )


def _persona_domain(value: str) -> CandidatePersonaDomain | None:
    try:
        return CandidatePersonaDomain(value)
    except ValueError:
        return None


def _background_subject(value: str) -> CandidateBackgroundSubject:
    try:
        return CandidateBackgroundSubject(value)
    except ValueError:
        return CandidateBackgroundSubject.NONE


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


def _generated_candidate(drafted: DraftedCandidate) -> GeneratedCandidate:
    """Carry one validated draft across into the callback's own candidate shape."""
    draft = drafted.draft
    image_inputs = draft.image_inputs
    return GeneratedCandidate(
        topic=draft.topic,
        country=draft.country,
        caption=draft.caption,
        hypothesis=draft.hypothesis,
        posting_slot=draft.posting_slot.value,
        persona_domain=None if draft.persona_domain is None else draft.persona_domain.value,
        refs_used=draft.refs_used,
        principles_applied=draft.principles_applied,
        appium_prompt=draft.appium_prompt,
        image_inputs=GeneratedImageInputs(
            trace_items=tuple(
                GeneratedScheduleEntry(
                    title=entry.title,
                    day=entry.day,
                    days=entry.days,
                    time=entry.time,
                    color=entry.color,
                )
                for entry in image_inputs.trace_items
            ),
            trace_todos=image_inputs.trace_todos,
            device_time=image_inputs.device_time,
            background_subject=image_inputs.background_subject.value,
            background_mood=image_inputs.background_mood,
            background_search_query=image_inputs.background_search_query,
            language=image_inputs.language,
        ),
    )


def _candidate_output(candidate: GeneratedCandidate, drafted: DraftedCandidate) -> JsonObject:
    image_inputs = candidate.image_inputs
    provenance = drafted.provenance
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
            "trace_items": [entry.model_dump() for entry in image_inputs.trace_items],
            "trace_todos": list(image_inputs.trace_todos),
            "device_time": image_inputs.device_time,
            "background_subject": image_inputs.background_subject,
            "background_mood": image_inputs.background_mood,
            "background_search_query": image_inputs.background_search_query,
            "language": image_inputs.language,
        },
        # Observed while the call ran, not reconstructed afterwards: which documents were
        # in the instruction and how big they were, how long the instruction actually was,
        # which reference bodies this one call read, and which form it was told to write.
        "provenance": {
            "documents": [
                {"relative_path": document.relative_path, "size_bytes": document.size_bytes}
                for document in provenance.documents
            ],
            "model": provenance.model,
            "instruction_chars": provenance.instruction_chars,
            "generated_at": provenance.generated_at,
            "assigned_domains": [domain.value for domain in provenance.assigned_domains],
            "reference_ids": list(provenance.reference_ids),
            "caption_form": drafted.caption_form.value,
            # What this candidate was told to start from, and how many candidates shared
            # the call that wrote it. The control plane's provenance normaliser keeps a
            # fixed set of keys and drops these two, so they survive in the task's stored
            # result rather than on the candidate row — which is where someone asking
            # "why did this batch converge" goes looking anyway.
            "assigned_interest": drafted.assigned_interest,
            "batch_size": provenance.batch_size,
        },
    }

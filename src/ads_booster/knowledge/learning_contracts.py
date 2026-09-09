"""Receipt-grounded shared-learning contracts."""

from __future__ import annotations

# These Pydantic field annotations are resolved at runtime.
# ruff: noqa: EM101, TC001
from enum import StrEnum, unique
from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId
from ads_booster.contracts.canonical import canonical_sha256
from ads_booster.contracts.models import Sha256Digest
from ads_booster.knowledge.contract_types import KnowledgeContractModel, UtcDatetime
from ads_booster.knowledge.evidence_contracts import EvidenceRef
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.operation_enums import ExperienceOutcome, LearningPurpose
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
EXPERIENCE_INPUT_EXCERPT_BYTES = 8_192
EXPERIENCE_OUTPUT_EXCERPT_BYTES = 16_384


@unique
class LearningRoundState(StrEnum):
    COLLECTING = "collecting"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@unique
class ExperienceEvidenceCompleteness(StrEnum):
    COMPLETE = "complete"
    TRUNCATED = "truncated"
    UNAVAILABLE = "unavailable"


class AgentExperienceEvidence(KnowledgeContractModel):
    """Bounded canonical invocation and result data; never instruction authority."""

    schema_version: Literal["knowledge.agent-experience-evidence.v1"] = Field(alias="schema")
    input_sha256: Sha256Digest
    output_sha256: Sha256Digest
    input_json_excerpt: Annotated[str, Field(max_length=EXPERIENCE_INPUT_EXCERPT_BYTES)] | None = (
        None
    )
    output_json_excerpt: (
        Annotated[str, Field(max_length=EXPERIENCE_OUTPUT_EXCERPT_BYTES)] | None
    ) = None
    input_completeness: ExperienceEvidenceCompleteness
    output_completeness: ExperienceEvidenceCompleteness

    @model_validator(mode="after")
    def require_digest_bound_complete_excerpts(self) -> Self:
        _require_excerpt(self.input_json_excerpt, self.input_completeness, self.input_sha256)
        _require_excerpt(self.output_json_excerpt, self.output_completeness, self.output_sha256)
        return self


class AgentExperienceRef(KnowledgeContractModel):
    """A terminal tool receipt bound to its stored run and source revision."""

    schema_version: Literal["knowledge.agent-experience-ref.v1"] = Field(alias="schema")
    experience_id: BoundedId
    workspace_id: BoundedId
    run_id: BoundedId
    invocation_id: BoundedId
    receipt_id: BoundedId
    source_digest: Sha256Digest
    outcome: ExperienceOutcome
    capability_id: BoundedId
    occurred_at: UtcDatetime
    applicability: AppliesTo
    evidence: AgentExperienceEvidence | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_payload(self, handler: SerializerFunctionWrapHandler) -> JsonObject:
        result = _JSON_OBJECT.validate_python(handler(self))
        if self.evidence is None:
            _ = result.pop("evidence", None)
        return result


class LearningCounter(KnowledgeContractModel):
    """Durable workspace wake-up state; it carries no grant authority."""

    schema_version: Literal["knowledge.learning-counter.v1"] = Field(alias="schema")
    workspace_id: BoundedId
    conversation_turns: Annotated[int, Field(ge=0)] = 0
    terminal_tool_receipts: Annotated[int, Field(ge=0)] = 0
    turn_watermark: BoundedId | None = None
    receipt_watermark: BoundedId | None = None
    round_id: BoundedId | None = None
    updated_at: UtcDatetime


class LearningRound(KnowledgeContractModel):
    """One sealed review window, partitioned later by the existing grants."""

    schema_version: Literal["knowledge.learning-round.v1"] = Field(alias="schema")
    round_id: BoundedId
    workspace_id: BoundedId
    purpose: LearningPurpose
    start_turn_watermark: BoundedId | None = None
    start_receipt_watermark: BoundedId | None = None
    end_turn_watermark: BoundedId | None = None
    end_receipt_watermark: BoundedId | None = None
    turn_count: Annotated[int, Field(ge=0)]
    receipt_count: Annotated[int, Field(ge=0)]
    experience_refs: Annotated[tuple[AgentExperienceRef, ...], Field(max_length=128)] = ()
    state: LearningRoundState
    created_at: UtcDatetime


class LearningReviewRequest(KnowledgeContractModel):
    """Source-bound input for a learning-purpose curation pass."""

    schema_version: Literal["knowledge.learning-review-request.v1"] = Field(alias="schema")
    round_id: BoundedId
    purpose: LearningPurpose
    user_event_refs: Annotated[tuple[EvidenceRef, ...], Field(max_length=128)] = ()
    experience_refs: Annotated[tuple[AgentExperienceRef, ...], Field(max_length=128)] = ()
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(max_length=128)] = ()
    applicability: AppliesTo
    expected_head_ids: Annotated[tuple[BoundedId, ...], Field(max_length=128)] = ()
    consumed_target_ids: Annotated[tuple[BoundedId, ...], Field(max_length=128)] = ()


__all__ = [
    "EXPERIENCE_INPUT_EXCERPT_BYTES",
    "EXPERIENCE_OUTPUT_EXCERPT_BYTES",
    "AgentExperienceEvidence",
    "AgentExperienceRef",
    "ExperienceEvidenceCompleteness",
    "ExperienceOutcome",
    "LearningCounter",
    "LearningPurpose",
    "LearningReviewRequest",
    "LearningRound",
    "LearningRoundState",
]


def _require_excerpt(
    excerpt: str | None,
    completeness: ExperienceEvidenceCompleteness,
    digest: str,
) -> None:
    match completeness:
        case ExperienceEvidenceCompleteness.COMPLETE:
            if excerpt is None or canonical_sha256(_JSON_OBJECT.validate_json(excerpt)) != digest:
                raise PydanticCustomError(
                    "experience_evidence_digest_mismatch",
                    "complete experience evidence must match its canonical digest",
                )
        case ExperienceEvidenceCompleteness.TRUNCATED:
            if not excerpt:
                raise PydanticCustomError(
                    "experience_evidence_excerpt_missing",
                    "truncated experience evidence requires a non-empty excerpt",
                )
        case ExperienceEvidenceCompleteness.UNAVAILABLE:
            if excerpt is not None:
                raise PydanticCustomError(
                    "experience_evidence_unavailable_payload",
                    "unavailable experience evidence cannot include an excerpt",
                )

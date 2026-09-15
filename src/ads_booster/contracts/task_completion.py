"""Exact deliverables and separately attributed completion judgments."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.agent_run import BoundedId, contract_sha256
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.contracts.task_instruction import TaskInstruction  # noqa: TC001
from ads_booster.transport.json_types import JsonObject  # noqa: TC001 - Pydantic field.


class CompletionCandidate(ContractModel):
    schema_version: Literal["trace.completion-candidate.v1"] = "trace.completion-candidate.v1"
    candidate_id: BoundedId
    task_id: BoundedId
    task_revision: Annotated[int, Field(ge=1)]
    answer: Annotated[str, Field(min_length=1, max_length=20_000)]
    answer_sha256: Sha256Digest
    result_links: tuple[str, ...] = ()
    attachment_refs: tuple[str, ...] = ()
    evidence_sha256s: tuple[Sha256Digest, ...] = ()

    @model_validator(mode="after")
    def require_answer_binding(self) -> Self:
        if self.answer_sha256 != contract_sha256({"answer": self.answer}):
            message = "completion candidate answer digest mismatch"
            raise ValueError(message)
        return self


class ObligationAssessment(ContractModel):
    obligation_id: BoundedId
    status: Literal["satisfied", "unsatisfied", "unverifiable", "superseded"]
    superseded_by_event_id: BoundedId | None = None
    mechanism: Annotated[str, Field(min_length=1, max_length=160)]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_sha256s: tuple[Sha256Digest, ...] = ()

    @model_validator(mode="after")
    def require_supersession_source(self) -> Self:
        if (self.status == "superseded") != (self.superseded_by_event_id is not None):
            message = "superseded assessment requires an admitted source event"
            raise ValueError(message)
        return self


class CompletionAssessment(ContractModel):
    schema_version: Literal["trace.task-completion.v1"] = "trace.task-completion.v1"
    assessment_id: BoundedId
    task_id: BoundedId
    task_revision: Annotated[int, Field(ge=1)]
    task_spec_sha256: Sha256Digest
    candidate_sha256: Sha256Digest
    evidence_sha256s: tuple[Sha256Digest, ...] = ()
    obligations: Annotated[tuple[ObligationAssessment, ...], Field(min_length=1)]
    disposition: Literal["satisfied", "continue", "waiting", "blocked"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    uncovered_requirements: Annotated[tuple[str, ...], Field(max_length=128)] = ()
    assessment_cache_sha256: Sha256Digest | None = None
    assessor_identity: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    semantic_result: SemanticAssessmentResult | None = None
    cache_hit: bool = False


class CompletionEvidenceSummary(ContractModel):
    evidence_sha256: Sha256Digest
    kind: Literal["response", "artifact", "effect"]
    description: Annotated[str, Field(min_length=1, max_length=4000)]
    verified: bool


class SemanticObligation(ContractModel):
    obligation_id: BoundedId
    kind: Literal["response", "artifact", "effect"]
    description: Annotated[str, Field(min_length=1, max_length=20_000)]
    required: bool


class SemanticAssessmentRequest(ContractModel):
    schema_version: Literal["trace.semantic-assessment-request.v1"] = (
        "trace.semantic-assessment-request.v1"
    )
    task_spec_sha256: Sha256Digest
    original_objective: str
    admitted_instructions: tuple[TaskInstruction, ...] = ()
    original_criteria: tuple[str, ...]
    objective: str
    constraints: tuple[str, ...]
    candidate: CompletionCandidate
    prior_result: CompletionCandidate | None = None
    obligations: tuple[SemanticObligation, ...]
    evidence: tuple[CompletionEvidenceSummary, ...] = ()
    evidence_sha256s: tuple[Sha256Digest, ...] = ()
    reference_context: JsonObject | None = None


class SemanticAssessmentResult(ContractModel):
    schema_version: Literal["trace.semantic-assessment-result.v1"] = (
        "trace.semantic-assessment-result.v1"
    )
    request_sha256: Sha256Digest
    candidate_sha256: Sha256Digest
    obligations: tuple[ObligationAssessment, ...]
    uncovered_requirements: tuple[str, ...] = ()
    required_evidence_kinds: tuple[Literal["artifact", "effect"], ...] = ()
    requested_deliverables_supported: bool

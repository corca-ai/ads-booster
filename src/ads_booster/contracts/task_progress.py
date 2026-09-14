"""Versioned host-admitted task state, independent of ledger revisions."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.agent_run import BoundedId  # noqa: TC001
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.contracts.task_completion import CompletionCandidate  # noqa: TC001
from ads_booster.contracts.task_instruction import TaskInstruction  # noqa: TC001


class ExactResponseCheck(ContractModel):
    kind: Literal["exact_response"] = "exact_response"
    expected: Annotated[str, Field(min_length=1, max_length=20_000)]


class ResponseLineCountCheck(ContractModel):
    kind: Literal["response_line_count"] = "response_line_count"
    expected: Annotated[int, Field(ge=1, le=10_000)]


class ResponseJsonFieldsCheck(ContractModel):
    kind: Literal["response_json_fields"] = "response_json_fields"
    required_fields: Annotated[tuple[str, ...], Field(min_length=1, max_length=128)]


class RequiredEvidenceCheck(ContractModel):
    kind: Literal["required_evidence"] = "required_evidence"
    evidence_sha256: Sha256Digest


DeterministicTaskCheck = Annotated[
    ExactResponseCheck | ResponseLineCountCheck | ResponseJsonFieldsCheck | RequiredEvidenceCheck,
    Field(discriminator="kind"),
]


class TaskObligation(ContractModel):
    obligation_id: BoundedId
    kind: Literal["response", "artifact", "effect"]
    description: Annotated[str, Field(min_length=1, max_length=20_000)]
    required: bool = True
    source_refs: Annotated[tuple[str, ...], Field(min_length=1)]
    verification: DeterministicTaskCheck | None = None


class TaskSpec(ContractModel):
    schema_version: Literal["trace.task-spec.v1"] = "trace.task-spec.v1"
    task_id: BoundedId
    task_revision: Annotated[int, Field(ge=1)]
    source_event_id: BoundedId
    prior_revision: Annotated[int, Field(ge=1)] | None = None
    objective: Annotated[str, Field(min_length=1, max_length=20_000)]
    original_objective: Annotated[str, Field(min_length=1, max_length=20_000)]
    prior_result: CompletionCandidate | None = None
    original_criteria: Annotated[tuple[str, ...], Field(min_length=1)]
    constraints: tuple[str, ...] = ()
    source_event_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    source_admission_sha256s: tuple[tuple[str, Sha256Digest], ...] = ()
    admitted_instructions: tuple[TaskInstruction, ...] = ()
    obligations: Annotated[tuple[TaskObligation, ...], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def require_lineage(self) -> Self:
        instruction_ids = tuple(item.source_event_id for item in self.admitted_instructions)
        if len(set(instruction_ids)) != len(instruction_ids) or not set(instruction_ids).issubset(
            self.source_event_ids
        ):
            message = "task instruction source lineage is invalid"
            raise ValueError(message)
        if instruction_ids and instruction_ids[-1] != self.source_event_id:
            message = "task latest instruction does not match admitted source"
            raise ValueError(message)
        ids = tuple(item.obligation_id for item in self.obligations)
        if len(set(ids)) != len(ids):
            message = "task obligation IDs must be unique"
            raise ValueError(message)
        if self.source_event_id not in self.source_event_ids:
            message = "task source missing from lineage"
            raise ValueError(message)
        if self.prior_revision != (self.task_revision - 1 or None):
            message = "task revision lineage is not consecutive"
            raise ValueError(message)
        if any(
            not set(item.source_refs).issubset(self.source_event_ids) for item in self.obligations
        ):
            message = "obligation source is not admitted"
            raise ValueError(message)
        return self


class TaskProposal(ContractModel):
    task_revision: Annotated[int, Field(ge=1)]
    source_event_id: BoundedId
    obligations: tuple[TaskObligation, ...]


class TaskPolicy(ContractModel):
    max_decision_calls: Annotated[int, Field(ge=1, le=10_000)] = 64
    max_assessments: Annotated[int, Field(ge=1, le=3)] = 3
    slice_provider_calls: Annotated[int, Field(ge=1, le=64)] = 4
    slice_seconds: Annotated[int, Field(ge=1)] = 20
    no_progress: Annotated[int, Field(ge=1)] = 3


class ObligationEvidence(ContractModel):
    obligation_id: BoundedId
    evidence_sha256s: tuple[Sha256Digest, ...]


class TaskCheckpoint(ContractModel):
    schema_version: Literal["trace.task-checkpoint.v1"] = "trace.task-checkpoint.v1"
    task_id: BoundedId
    task_revision: Annotated[int, Field(ge=1)]
    spec_sha256: Sha256Digest
    segment_id: BoundedId
    policy: TaskPolicy = Field(default_factory=TaskPolicy)
    disposition: Literal[
        "active", "satisfied", "waiting", "blocked", "budget_exhausted", "cancelled", "superseded"
    ] = "active"
    accepted_evidence: tuple[ObligationEvidence, ...] = ()
    unresolved_obligation_ids: tuple[BoundedId, ...]
    candidate: CompletionCandidate | None = None
    decision_calls: Annotated[int, Field(ge=0)] = 0
    assessment_calls: Annotated[int, Field(ge=0)] = 0
    active_elapsed_ms: Annotated[int, Field(ge=0)] = 0
    fingerprints: Annotated[tuple[Sha256Digest, ...], Field(max_length=8)] = ()
    reconsideration_used: bool = False
    strategy_feedback: str | None = None
    progress_evidence_sha256s: tuple[Sha256Digest, ...] = ()
    progress_obligation_ids: tuple[BoundedId, ...] = ()
    next_action: Literal["plan", "execute", "assess", "wait", "done"] | None = "plan"
    wait_reason: str | None = None

    @model_validator(mode="after")
    def require_counters_and_candidate(self) -> Self:
        if (
            self.decision_calls > self.policy.max_decision_calls
            or self.assessment_calls > self.policy.max_assessments
        ):
            message = "task checkpoint exceeds frozen policy"
            raise ValueError(message)
        if self.candidate is not None and (
            self.candidate.task_id,
            self.candidate.task_revision,
        ) != (self.task_id, self.task_revision):
            message = "checkpoint candidate belongs to another task revision"
            raise ValueError(message)
        return self

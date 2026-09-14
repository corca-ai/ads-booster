"""Replaceable reasoning-provider contracts for the Marketing Agent core."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.agent_run import (
    AgentGoal,
    BoundedId,
    CapabilitySnapshot,
    contract_sha256,
)
from ads_booster.contracts.knowledge_preparation import PreparedKnowledgeContext  # noqa: TC001
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind  # noqa: TC001
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.contracts.task_completion import (  # noqa: TC001
    CompletionAssessment,
    CompletionCandidate,
)
from ads_booster.contracts.task_progress import (  # noqa: TC001
    TaskCheckpoint,
    TaskProposal,
    TaskSpec,
)
from ads_booster.transport.json_types import JsonObject  # noqa: TC001


class ReasoningRequest(ContractModel):
    schema_version: Literal["trace.reasoning-request.v1"]
    run_id: Annotated[str, Field(min_length=1, max_length=160)]
    phase: Literal["plan", "replan"]
    goal: AgentGoal
    # Host-admitted task input, separate from untrusted observations and the original goal.
    current_user_message: Annotated[str, Field(min_length=1, max_length=20_000)] | None = None
    capability_snapshot: CapabilitySnapshot
    evidence: Annotated[tuple[JsonObject, ...], Field(max_length=128)] = ()
    remaining_tool_calls: Annotated[int, Field(ge=0, le=10_000)]
    remaining_cost_units: Annotated[int, Field(ge=0, le=1_000_000)]
    prepared_context: PreparedKnowledgeContext | None = None


class ReasoningDecision(ContractModel):
    schema_version: Literal["trace.reasoning-decision.v1"]
    action: Literal["invoke_tool", "request_input", "stop"]
    capability_id: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    tool_input: JsonObject | None = None
    expected_outcome: Annotated[str, Field(min_length=1, max_length=2000)]
    reasoning_summary: Annotated[str, Field(min_length=1, max_length=4000)]
    proposed_action_kind: KnowledgeActionKind | None = None
    proposed_brand_ref: BoundedId | None = None

    @model_validator(mode="after")
    def require_action_payload(self) -> Self:
        invokes = self.action == "invoke_tool"
        if invokes != (self.capability_id is not None and self.tool_input is not None):
            message = "reasoning tool action payload is inconsistent"
            raise ValueError(message)
        if not invokes and (self.capability_id is not None or self.tool_input is not None):
            message = "non-tool reasoning decision cannot include a tool"
            raise ValueError(message)
        if self.proposed_brand_ref is not None and self.proposed_action_kind is None:
            message = "reasoning brand proposal requires an action proposal"
            raise ValueError(message)
        return self


class ReasoningProviderReceipt(ContractModel):
    schema_version: Literal["trace.reasoning-provider-receipt.v1"]
    provider_id: Annotated[str, Field(min_length=1, max_length=160)]
    model_id: Annotated[str, Field(min_length=1, max_length=240)]
    request_sha256: Sha256Digest
    output_schema_sha256: Sha256Digest
    decision_sha256: Sha256Digest


class ReasoningResult(ContractModel):
    schema_version: Literal["trace.reasoning-result.v1"]
    decision: ReasoningDecision
    receipt: ReasoningProviderReceipt

    @model_validator(mode="after")
    def require_decision_binding(self) -> Self:
        _require_decision_binding(self.decision, self.receipt)
        return self


class ReasoningRequestV2(ContractModel):
    schema_version: Literal["trace.reasoning-request.v2"] = "trace.reasoning-request.v2"
    run_id: BoundedId
    phase: Literal["plan", "replan"]
    goal: AgentGoal
    current_user_message: Annotated[str, Field(min_length=1, max_length=20_000)] | None = None
    capability_snapshot: CapabilitySnapshot
    evidence: Annotated[tuple[JsonObject, ...], Field(max_length=128)] = ()
    remaining_tool_calls: Annotated[int, Field(ge=0, le=10_000)]
    remaining_cost_units: Annotated[int, Field(ge=0, le=1_000_000)]
    prepared_context: PreparedKnowledgeContext | None = None
    task: TaskSpec
    checkpoint: TaskCheckpoint
    completion_feedback: CompletionAssessment | None = None


class ReasoningDecisionV2(ContractModel):
    schema_version: Literal["trace.reasoning-decision.v2"] = "trace.reasoning-decision.v2"
    action: Literal["invoke_tool", "request_input", "stop"]
    capability_id: BoundedId | None = None
    tool_input: JsonObject | None = None
    expected_outcome: Annotated[str, Field(min_length=1, max_length=2000)]
    reasoning_summary: Annotated[str, Field(min_length=1, max_length=4000)]
    proposed_action_kind: KnowledgeActionKind | None = None
    proposed_brand_ref: BoundedId | None = None
    task_proposal: TaskProposal | None = None
    completion_candidate: CompletionCandidate | None = None

    @model_validator(mode="after")
    def require_action_payload(self) -> Self:
        _ = ReasoningDecision.model_validate(
            {
                **self.model_dump(exclude={"task_proposal", "completion_candidate"}),
                "schema_version": "trace.reasoning-decision.v1",
            }
        )
        if (self.action == "stop") != (self.completion_candidate is not None):
            message = "only stop decisions require a completion candidate"
            raise ValueError(message)
        return self


class ReasoningResultV2(ContractModel):
    schema_version: Literal["trace.reasoning-result.v2"] = "trace.reasoning-result.v2"
    decision: ReasoningDecisionV2
    receipt: ReasoningProviderReceipt

    @model_validator(mode="after")
    def require_decision_binding(self) -> Self:
        _require_decision_binding(self.decision, self.receipt)
        return self


def _require_decision_binding(
    decision: ReasoningDecision | ReasoningDecisionV2,
    receipt: ReasoningProviderReceipt,
) -> None:
    if contract_sha256(decision) != receipt.decision_sha256:
        message = "reasoning receipt decision digest mismatch"
        raise ValueError(message)


def decode_reasoning_decision(payload: JsonObject) -> ReasoningDecision | ReasoningDecisionV2:
    if payload.get("schema_version") == "trace.reasoning-decision.v2":
        return ReasoningDecisionV2.model_validate(payload)
    return ReasoningDecision.model_validate(payload)


def decode_reasoning_result(payload: JsonObject) -> ReasoningResult | ReasoningResultV2:
    if payload.get("schema_version") == "trace.reasoning-result.v2":
        return ReasoningResultV2.model_validate(payload)
    return ReasoningResult.model_validate(payload)


def decode_reasoning_request(payload: JsonObject) -> ReasoningRequest | ReasoningRequestV2:
    if payload.get("schema_version") == "trace.reasoning-request.v2":
        return ReasoningRequestV2.model_validate(payload)
    return ReasoningRequest.model_validate(payload)


__all__ = [
    "ReasoningDecision",
    "ReasoningProviderReceipt",
    "ReasoningRequest",
    "ReasoningResult",
]

"""Replaceable reasoning-provider contracts for the Marketing Agent core."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.agent_run import AgentGoal, CapabilitySnapshot, contract_sha256
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.transport.json_types import JsonObject  # noqa: TC001


class ReasoningRequest(ContractModel):
    schema_version: Literal["trace.reasoning-request.v1"]
    run_id: Annotated[str, Field(min_length=1, max_length=160)]
    phase: Literal["plan", "replan"]
    goal: AgentGoal
    capability_snapshot: CapabilitySnapshot
    evidence: Annotated[tuple[JsonObject, ...], Field(max_length=128)] = ()
    remaining_tool_calls: Annotated[int, Field(ge=0, le=10_000)]
    remaining_cost_units: Annotated[int, Field(ge=0, le=1_000_000)]


class ReasoningDecision(ContractModel):
    schema_version: Literal["trace.reasoning-decision.v1"]
    action: Literal["invoke_tool", "request_input", "stop"]
    capability_id: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    tool_input: JsonObject | None = None
    expected_outcome: Annotated[str, Field(min_length=1, max_length=2000)]
    reasoning_summary: Annotated[str, Field(min_length=1, max_length=4000)]

    @model_validator(mode="after")
    def require_action_payload(self) -> Self:
        invokes = self.action == "invoke_tool"
        if invokes != (self.capability_id is not None and self.tool_input is not None):
            message = "reasoning tool action payload is inconsistent"
            raise ValueError(message)
        if not invokes and (self.capability_id is not None or self.tool_input is not None):
            message = "non-tool reasoning decision cannot include a tool"
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
        if contract_sha256(self.decision) != self.receipt.decision_sha256:
            message = "reasoning receipt decision digest mismatch"
            raise ValueError(message)
        return self


__all__ = [
    "ReasoningDecision",
    "ReasoningProviderReceipt",
    "ReasoningRequest",
    "ReasoningResult",
]

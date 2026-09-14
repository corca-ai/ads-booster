"""Ports implemented by replaceable reasoning and execution providers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ads_booster.contracts.agent_run import ToolExecutionDeferred, ToolInvocation
    from ads_booster.contracts.reasoning import (
        ReasoningRequest,
        ReasoningRequestV2,
        ReasoningResult,
        ReasoningResultV2,
    )
    from ads_booster.contracts.task_completion import (
        SemanticAssessmentRequest,
        SemanticAssessmentResult,
    )
    from ads_booster.contracts.tool_capability import ToolDescriptor, ToolExecutionResult


class ReasoningProvider(Protocol):
    def plan(self, request: ReasoningRequest) -> ReasoningResult: ...


@runtime_checkable
class ReasoningProviderV2(Protocol):
    def plan_v2(self, request: ReasoningRequestV2) -> ReasoningResultV2: ...


class SemanticAssessor(Protocol):
    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult: ...


@runtime_checkable
class IdentifiedSemanticAssessor(Protocol):
    @property
    def assessment_identity(self) -> str: ...


class ToolAdapter(Protocol):
    def execute(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> ToolExecutionResult | ToolExecutionDeferred: ...


__all__ = ["ReasoningProvider", "ToolAdapter"]

"""Ports implemented by replaceable reasoning and execution providers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult

if TYPE_CHECKING:
    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor, ToolExecutionResult


class ReasoningProvider(Protocol):
    def plan(self, request: ReasoningRequest) -> ReasoningResult: ...


class ToolAdapter(Protocol):
    def execute(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> ToolExecutionResult: ...


__all__ = ["ReasoningProvider", "ToolAdapter"]

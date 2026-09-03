"""Thin execution boundary around automation owned by existing packages.

The bridge deliberately owns no run state, reasoning, approval, retry, or
reconciliation decisions.  Those remain responsibilities of Agent Core.  A
composition root injects an existing executor through ``ToolExecutor``.
"""

from __future__ import annotations

from typing import Literal, Protocol, final

from pydantic import Field

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.tool_capability import ToolDescriptor, ToolExecutionResult
from ads_booster.transport.json_types import JsonObject


class ToolDelegationError(RuntimeError):
    """Raised before delegation when the frozen invocation binding is invalid."""


class DelegatedToolResult(ContractModel):
    """Small result seam implemented by an existing automation owner."""

    disposition: Literal["no_effect", "succeeded", "failed"]
    output: JsonObject
    actual_cost_units: int = Field(ge=0, le=1_000_000)


class ToolExecutor(Protocol):
    """Injected seam; implementations remain in their existing owner package."""

    def __call__(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> DelegatedToolResult: ...


@final
class DelegatingToolAdapter:
    """Bind one existing executor to one installed capability version."""

    def __init__(
        self,
        *,
        capability_id: str,
        version: str,
        executor_id: str,
        executor: ToolExecutor,
    ) -> None:
        self._capability_id = capability_id
        self._version = version
        self._executor_id = executor_id
        self._executor = executor

    def execute(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> ToolExecutionResult:
        self._validate_binding(invocation, descriptor)
        delegated = self._executor(invocation, descriptor)
        return ToolExecutionResult(
            schema_version="trace.tool-execution-result.v1",
            disposition=delegated.disposition,
            invocation_sha256=contract_sha256(invocation),
            output=delegated.output,
            actual_cost_units=delegated.actual_cost_units,
            executor_id=self._executor_id,
        )

    def _validate_binding(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> None:
        if descriptor.capability_id != self._capability_id:
            message = "tool adapter capability does not match the frozen descriptor"
            raise ToolDelegationError(message)
        if descriptor.version != self._version:
            message = "tool adapter version does not match the frozen descriptor"
            raise ToolDelegationError(message)
        if invocation.descriptor_sha256 != contract_sha256(descriptor):
            message = "tool invocation is not bound to the supplied frozen descriptor"
            raise ToolDelegationError(message)
        if not descriptor.enabled or not descriptor.readiness.ready:
            message = "unavailable tool descriptor cannot be delegated"
            raise ToolDelegationError(message)


def research_adapter(
    *, executor_id: str, executor: ToolExecutor, version: str = "1"
) -> DelegatingToolAdapter:
    return _adapter("research.web", version, executor_id, executor)


def candidate_adapter(
    *, executor_id: str, executor: ToolExecutor, version: str = "1"
) -> DelegatingToolAdapter:
    return _adapter("creative.candidates.generate", version, executor_id, executor)


def appium_adapter(
    *, executor_id: str, executor: ToolExecutor, version: str = "1"
) -> DelegatingToolAdapter:
    return _adapter("capture.appium", version, executor_id, executor)


def capture_adapter(
    *, executor_id: str, executor: ToolExecutor, version: str = "1"
) -> DelegatingToolAdapter:
    return _adapter("capture.native_png", version, executor_id, executor)


def threads_adapter(
    *, executor_id: str, executor: ToolExecutor, version: str = "1"
) -> DelegatingToolAdapter:
    return _adapter("threads.publish", version, executor_id, executor)


def _adapter(
    capability_id: str,
    version: str,
    executor_id: str,
    executor: ToolExecutor,
) -> DelegatingToolAdapter:
    return DelegatingToolAdapter(
        capability_id=capability_id,
        version=version,
        executor_id=executor_id,
        executor=executor,
    )


__all__ = [
    "DelegatedToolResult",
    "DelegatingToolAdapter",
    "ToolDelegationError",
    "ToolExecutor",
    "appium_adapter",
    "candidate_adapter",
    "capture_adapter",
    "research_adapter",
    "threads_adapter",
]

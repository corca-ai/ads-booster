from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import pytest

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.tool_capability import EffectClass, ToolDescriptor
from ads_booster.marketing.tool_adapters import (
    DelegatedToolResult,
    DelegatingToolAdapter,
    ToolDelegationError,
    ToolExecutor,
    appium_adapter,
    appium_descriptor,
    candidate_adapter,
    candidate_descriptor,
    capture_adapter,
    capture_descriptor,
    research_adapter,
    research_descriptor,
    threads_adapter,
    threads_descriptor,
)

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 3, 1, 2, 3, tzinfo=UTC)


class DescriptorFactory(Protocol):
    def __call__(
        self,
        *,
        installation_id: str,
        observed_at: datetime,
        ready: bool,
        reason_code: str | None = None,
        version: str = "1",
    ) -> ToolDescriptor: ...


class AdapterFactory(Protocol):
    def __call__(
        self,
        *,
        executor_id: str,
        executor: ToolExecutor,
        version: str = "1",
    ) -> DelegatingToolAdapter: ...


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[ToolInvocation, ToolDescriptor]] = []

    def __call__(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> DelegatedToolResult:
        self.calls.append((invocation, descriptor))
        return DelegatedToolResult(
            disposition="succeeded",
            output={"artifact_id": "artifact-one", "delegated": True},
            actual_cost_units=3,
        )


def test_adapter_delegates_exact_frozen_contracts_and_binds_result() -> None:
    descriptor = research_descriptor(
        installation_id="on-prem-research",
        observed_at=NOW,
        ready=True,
    )
    invocation = _invocation(descriptor)
    executor = RecordingExecutor()
    adapter = DelegatingToolAdapter(
        capability_id=descriptor.capability_id,
        version=descriptor.version,
        executor_id="existing-research-owner",
        executor=executor,
    )

    result = adapter.execute(invocation, descriptor)

    assert executor.calls == [(invocation, descriptor)]
    assert executor.calls[0][0] is invocation
    assert executor.calls[0][1] is descriptor
    assert result.invocation_sha256 == contract_sha256(invocation)
    assert result.executor_id == "existing-research-owner"
    assert result.output == {"artifact_id": "artifact-one", "delegated": True}
    assert result.actual_cost_units == 3


def test_adapter_refuses_descriptor_substitution_before_delegation() -> None:
    descriptor = research_descriptor(
        installation_id="on-prem-research",
        observed_at=NOW,
        ready=True,
    )
    invocation = _invocation(descriptor)
    substituted = research_descriptor(
        installation_id="different-installation",
        observed_at=NOW,
        ready=True,
    )
    executor = RecordingExecutor()
    adapter = DelegatingToolAdapter(
        capability_id=descriptor.capability_id,
        version=descriptor.version,
        executor_id="existing-research-owner",
        executor=executor,
    )

    with pytest.raises(ToolDelegationError, match="not bound"):
        _ = adapter.execute(invocation, substituted)

    assert executor.calls == []


def test_unavailable_worker_descriptor_cannot_be_delegated() -> None:
    unavailable = appium_descriptor(
        installation_id="mac-worker-one",
        observed_at=NOW,
        ready=False,
        reason_code="appium_unavailable",
    )
    invocation = _invocation(unavailable)
    executor = RecordingExecutor()
    adapter = DelegatingToolAdapter(
        capability_id=unavailable.capability_id,
        version=unavailable.version,
        executor_id="mac-worker-one",
        executor=executor,
    )

    with pytest.raises(ToolDelegationError, match="unavailable"):
        _ = adapter.execute(invocation, unavailable)

    assert executor.calls == []


@pytest.mark.parametrize(
    ("factory", "capability_id", "owner", "effect_class"),
    [
        (
            research_descriptor,
            "research.web",
            "ads_booster.marketing.dynamic_evidence_research",
            EffectClass.OBSERVE,
        ),
        (
            candidate_descriptor,
            "creative.candidates.generate",
            "ads_booster.candidate_generation",
            EffectClass.LOCAL_ARTIFACT,
        ),
        (
            appium_descriptor,
            "capture.appium",
            "ads_booster.capture",
            EffectClass.EXTERNAL,
        ),
        (
            capture_descriptor,
            "capture.native_png",
            "ads_booster.marketing.native_capture",
            EffectClass.LOCAL_ARTIFACT,
        ),
        (
            threads_descriptor,
            "threads.publish",
            "ads_booster.marketing.threads",
            EffectClass.EXTERNAL,
        ),
    ],
)
def test_existing_automation_descriptors_keep_owner_and_effect_boundary(
    factory: DescriptorFactory,
    capability_id: str,
    owner: str,
    effect_class: EffectClass,
) -> None:
    descriptor = factory(
        installation_id="installed-owner",
        observed_at=NOW,
        ready=True,
    )

    assert descriptor.capability_id == capability_id
    assert descriptor.owner == owner
    assert descriptor.effect_class is effect_class
    assert descriptor.approval_policy.mode == (
        "none" if effect_class is EffectClass.OBSERVE else "required"
    )
    assert descriptor.readiness.ready is True
    assert descriptor.input_schema_sha256 == contract_sha256(descriptor.input_schema)
    assert descriptor.output_schema_sha256 == contract_sha256(descriptor.output_schema)
    assert descriptor.config_schema_sha256 == contract_sha256(descriptor.config_schema)
    assert descriptor.receipt_schema_sha256 == contract_sha256(descriptor.receipt_schema)


def test_threads_publish_keeps_readback_reconciliation_boundary() -> None:
    descriptor = threads_descriptor(
        installation_id="threads-account-one",
        observed_at=NOW,
        ready=True,
    )

    assert descriptor.reconciliation.mode == "readback"
    assert descriptor.reconciliation.lookup_capability_id == "threads.readback"
    assert descriptor.credential_boundary == "adapter_owner"


@pytest.mark.parametrize(
    ("adapter_factory", "descriptor_factory"),
    [
        (research_adapter, research_descriptor),
        (candidate_adapter, candidate_descriptor),
        (appium_adapter, appium_descriptor),
        (capture_adapter, capture_descriptor),
        (threads_adapter, threads_descriptor),
    ],
)
def test_named_adapter_constructors_delegate_only_their_owner_capability(
    adapter_factory: AdapterFactory,
    descriptor_factory: DescriptorFactory,
) -> None:
    descriptor = descriptor_factory(
        installation_id="installed-owner",
        observed_at=NOW,
        ready=True,
    )
    invocation = _invocation(descriptor)
    executor = RecordingExecutor()
    adapter = adapter_factory(
        executor_id="installed-owner",
        executor=executor,
    )

    result = adapter.execute(invocation, descriptor)

    assert result.invocation_sha256 == contract_sha256(invocation)
    assert executor.calls == [(invocation, descriptor)]


def _invocation(descriptor: ToolDescriptor) -> ToolInvocation:
    tool_input: JsonObject = {"query": "AI lock-screen launch formats"}
    return ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        invocation_id="invocation-one",
        run_id="run-one",
        step_id="step-one",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256=contract_sha256(descriptor),
        idempotency_key="run-one:research.web:one",
        input=tool_input,
        input_sha256=contract_sha256(tool_input),
    )

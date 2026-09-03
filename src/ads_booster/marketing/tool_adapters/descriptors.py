"""Tool descriptors for existing Trace marketing automation owners."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.tool_capability import (
    EffectClass,
    ToolApprovalPolicy,
    ToolCost,
    ToolDescriptor,
    ToolIdempotencyPolicy,
    ToolReadiness,
    ToolReconciliationPolicy,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from datetime import datetime

_OBJECT_SCHEMA: JsonObject = {
    "type": "object",
    "additionalProperties": True,
}
_SLACK_INPUT_SCHEMA: JsonObject = {
    "type": "object",
    "required": ["text"],
    "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 40000}},
    "additionalProperties": False,
}
_NOTION_DAILY_INPUT_SCHEMA: JsonObject = {
    "type": "object",
    "required": ["title", "content"],
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 200},
        "content": {"type": "string", "minLength": 1, "maxLength": 2000},
    },
    "additionalProperties": False,
}
_HOSTED_INSTALL_INPUT_SCHEMA: JsonObject = {
    "type": "object",
    "required": ["capability_id"],
    "properties": {
        "capability_id": {
            "enum": ["research.web", "capture.native_png", "publish.threads", "deliver.slack"]
        }
    },
    "additionalProperties": False,
}
_RECEIPT_SCHEMA: JsonObject = {
    "type": "object",
    "required": [
        "schema_version",
        "disposition",
        "invocation_sha256",
        "output",
        "actual_cost_units",
        "executor_id",
    ],
    "properties": {
        "schema_version": {"const": "trace.tool-execution-result.v1"},
        "disposition": {"enum": ["no_effect", "succeeded", "failed"]},
        "invocation_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "output": {"type": "object"},
        "actual_cost_units": {"type": "integer", "minimum": 0},
        "executor_id": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class _DescriptorSpec:
    capability_id: str
    owner: str
    effect_class: EffectClass
    worst_case_units: int
    cost_unit: str
    credential_boundary: Literal["none", "adapter_owner"]
    reconciliation_mode: Literal["none", "readback", "manual"] | None = None
    lookup_capability_id: str | None = None


@dataclass(frozen=True)
class _InstallationState:
    installation_id: str
    observed_at: datetime
    ready: bool
    reason_code: str | None
    version: str


def research_descriptor(
    *,
    installation_id: str,
    observed_at: datetime,
    ready: bool,
    reason_code: str | None = None,
    version: str = "1",
) -> ToolDescriptor:
    return _descriptor(
        _DescriptorSpec(
            capability_id="research.web",
            owner="ads_booster.marketing.dynamic_evidence_research",
            effect_class=EffectClass.OBSERVE,
            worst_case_units=24,
            cost_unit="research_request",
            credential_boundary="adapter_owner",
        ),
        _InstallationState(installation_id, observed_at, ready, reason_code, version),
    )


def candidate_descriptor(
    *,
    installation_id: str,
    observed_at: datetime,
    ready: bool,
    reason_code: str | None = None,
    version: str = "1",
) -> ToolDescriptor:
    return _descriptor(
        _DescriptorSpec(
            capability_id="creative.candidates.generate",
            owner="ads_booster.candidate_generation",
            effect_class=EffectClass.LOCAL_ARTIFACT,
            worst_case_units=10,
            cost_unit="candidate_batch",
            credential_boundary="adapter_owner",
        ),
        _InstallationState(installation_id, observed_at, ready, reason_code, version),
    )


def appium_descriptor(
    *,
    installation_id: str,
    observed_at: datetime,
    ready: bool,
    reason_code: str | None = None,
    version: str = "1",
) -> ToolDescriptor:
    return _descriptor(
        _DescriptorSpec(
            capability_id="capture.appium",
            owner="ads_booster.capture",
            effect_class=EffectClass.EXTERNAL,
            worst_case_units=20,
            cost_unit="device_session",
            credential_boundary="adapter_owner",
        ),
        _InstallationState(installation_id, observed_at, ready, reason_code, version),
    )


def capture_descriptor(
    *,
    installation_id: str,
    observed_at: datetime,
    ready: bool,
    reason_code: str | None = None,
    version: str = "1",
) -> ToolDescriptor:
    return _descriptor(
        _DescriptorSpec(
            capability_id="capture.native_png",
            owner="ads_booster.marketing.native_capture",
            effect_class=EffectClass.LOCAL_ARTIFACT,
            worst_case_units=20,
            cost_unit="capture",
            credential_boundary="adapter_owner",
        ),
        _InstallationState(installation_id, observed_at, ready, reason_code, version),
    )


def threads_descriptor(
    *,
    installation_id: str,
    observed_at: datetime,
    ready: bool,
    reason_code: str | None = None,
    version: str = "1",
) -> ToolDescriptor:
    return _descriptor(
        _DescriptorSpec(
            capability_id="publish.threads",
            owner="ads_booster.marketing.threads",
            effect_class=EffectClass.EXTERNAL,
            worst_case_units=10,
            cost_unit="post",
            credential_boundary="adapter_owner",
            reconciliation_mode="readback",
            lookup_capability_id="threads.readback",
        ),
        _InstallationState(installation_id, observed_at, ready, reason_code, version),
    )


def hosted_workflow_descriptor(
    *, installation_id: str, observed_at: datetime, ready: bool, reason_code: str | None = None
) -> ToolDescriptor:
    return _descriptor(
        _DescriptorSpec(
            capability_id="workflow.feature_launch",
            owner="trace.hosted_marketing_workflow",
            effect_class=EffectClass.CONTROL_PLANE_WRITE,
            worst_case_units=40,
            cost_unit="workflow_run",
            credential_boundary="adapter_owner",
            reconciliation_mode="readback",
            lookup_capability_id="workflow.feature_launch.readback",
        ),
        _InstallationState(installation_id, observed_at, ready, reason_code, "1"),
    )


def hosted_tool_install_descriptor(
    *, installation_id: str, observed_at: datetime, ready: bool, reason_code: str | None = None
) -> ToolDescriptor:
    return _descriptor(
        _DescriptorSpec(
            capability_id="catalog.hosted.install",
            owner="trace.hosted_tool_catalog",
            effect_class=EffectClass.CONTROL_PLANE_WRITE,
            worst_case_units=1,
            cost_unit="catalog_registration",
            credential_boundary="adapter_owner",
        ),
        _InstallationState(installation_id, observed_at, ready, reason_code, "1"),
        input_schema=_HOSTED_INSTALL_INPUT_SCHEMA,
    )


def slack_delivery_descriptor(
    *, installation_id: str, observed_at: datetime, ready: bool, reason_code: str | None = None
) -> ToolDescriptor:
    return _descriptor(
        _DescriptorSpec(
            capability_id="deliver.slack",
            owner="slack.delivery",
            effect_class=EffectClass.EXTERNAL,
            worst_case_units=1,
            cost_unit="message",
            credential_boundary="adapter_owner",
        ),
        _InstallationState(installation_id, observed_at, ready, reason_code, "1"),
        input_schema=_SLACK_INPUT_SCHEMA,
    )


def notion_daily_descriptor(
    *, installation_id: str, observed_at: datetime, ready: bool, reason_code: str | None = None
) -> ToolDescriptor:
    return _descriptor(
        _DescriptorSpec(
            capability_id="store.notion.daily",
            owner="notion.daily_marketing_archive",
            effect_class=EffectClass.EXTERNAL,
            worst_case_units=1,
            cost_unit="page",
            credential_boundary="adapter_owner",
        ),
        _InstallationState(installation_id, observed_at, ready, reason_code, "1"),
        input_schema=_NOTION_DAILY_INPUT_SCHEMA,
    )


def _descriptor(
    spec: _DescriptorSpec,
    state: _InstallationState,
    *,
    input_schema: JsonObject = _OBJECT_SCHEMA,
) -> ToolDescriptor:
    reason_code = state.reason_code
    if state.ready and reason_code is not None:
        message = "ready tool descriptors cannot include an unavailable reason"
        raise ValueError(message)
    if not state.ready and reason_code is None:
        reason_code = "adapter_unavailable"
    reconciliation_mode = spec.reconciliation_mode
    if reconciliation_mode is None:
        reconciliation_mode = "none" if spec.effect_class is EffectClass.OBSERVE else "manual"
    return ToolDescriptor(
        schema_version="trace.tool-descriptor.v1",
        capability_id=spec.capability_id,
        version=state.version,
        owner=spec.owner,
        installation_id=state.installation_id,
        input_schema=input_schema,
        input_schema_sha256=contract_sha256(input_schema),
        output_schema=_OBJECT_SCHEMA,
        output_schema_sha256=contract_sha256(_OBJECT_SCHEMA),
        config_schema=_OBJECT_SCHEMA,
        config_schema_sha256=contract_sha256(_OBJECT_SCHEMA),
        receipt_schema=_RECEIPT_SCHEMA,
        receipt_schema_sha256=contract_sha256(_RECEIPT_SCHEMA),
        credential_boundary=spec.credential_boundary,
        effect_class=spec.effect_class,
        approval_policy=ToolApprovalPolicy(
            mode="none" if spec.effect_class is EffectClass.OBSERVE else "required"
        ),
        cost=ToolCost(worst_case_units=spec.worst_case_units, unit=spec.cost_unit),
        readiness=ToolReadiness(
            ready=state.ready,
            reason_code=reason_code,
            observed_at=state.observed_at,
            max_age_seconds=300,
        ),
        idempotency=ToolIdempotencyPolicy(key_scope="run_tool_input"),
        reconciliation=ToolReconciliationPolicy(
            mode=reconciliation_mode,
            lookup_capability_id=spec.lookup_capability_id,
            terminal_dispositions=(
                ("no_effect", "succeeded", "failed")
                if spec.effect_class is EffectClass.OBSERVE
                else ("no_effect", "succeeded", "failed", "unknown_side_effect")
            ),
        ),
    )


__all__ = [
    "appium_descriptor",
    "candidate_descriptor",
    "capture_descriptor",
    "hosted_tool_install_descriptor",
    "hosted_workflow_descriptor",
    "notion_daily_descriptor",
    "research_descriptor",
    "slack_delivery_descriptor",
    "threads_descriptor",
]

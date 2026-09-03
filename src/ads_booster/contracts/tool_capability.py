"""Provider-neutral contracts for tools selectable by the Marketing Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ads_booster.contracts.canonical import canonical_sha256
from ads_booster.contracts.models import ContractModel, Identifier
from ads_booster.transport.json_types import JsonObject  # noqa: TC001


class EffectClass(StrEnum):
    OBSERVE = "observe"
    LOCAL_ARTIFACT = "local_artifact"
    CONTROL_PLANE_WRITE = "control_plane_write"
    EXTERNAL = "external"


class ToolApprovalPolicy(ContractModel):
    mode: Literal["none", "required"]
    authority: Annotated[str, Field(min_length=1, max_length=160)] = "workspace_member"


class ToolCost(ContractModel):
    worst_case_units: Annotated[int, Field(ge=0, le=1_000_000)]
    unit: Annotated[str, Field(min_length=1, max_length=80)]


class ToolReadiness(ContractModel):
    ready: bool
    reason_code: Annotated[
        str,
        Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_]*$"),
    ] | None = None
    observed_at: datetime
    max_age_seconds: Annotated[int, Field(ge=1, le=86_400)]

    @model_validator(mode="after")
    def require_consistent_state(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != UTC.utcoffset(None):
            message = "tool readiness time must be UTC"
            raise ValueError(message)
        if self.ready == (self.reason_code is not None):
            message = "ready tools have no reason; unavailable tools require one"
            raise ValueError(message)
        return self


class ToolIdempotencyPolicy(ContractModel):
    key_scope: Literal["run_tool_input", "tenant_tool_input", "adapter_defined"]
    duplicate_disposition: Literal["reject_duplicate"] = "reject_duplicate"


class ToolReconciliationPolicy(ContractModel):
    mode: Literal["none", "readback", "manual"]
    lookup_capability_id: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    terminal_dispositions: Annotated[
        tuple[Literal["no_effect", "succeeded", "failed", "unknown_side_effect"], ...],
        Field(min_length=1, max_length=4),
    ]

    @model_validator(mode="after")
    def require_lookup_for_readback(self) -> Self:
        if (self.mode == "readback") != (self.lookup_capability_id is not None):
            message = "readback reconciliation requires exactly one lookup capability"
            raise ValueError(message)
        return self


class ToolDescriptor(ContractModel):
    """Complete definition and live installation state for one selectable tool version."""

    schema_version: Literal["trace.tool-descriptor.v1"]
    capability_id: Annotated[str, Field(min_length=1, max_length=160)]
    version: Identifier
    owner: Annotated[str, Field(min_length=1, max_length=160)]
    installation_id: Annotated[str, Field(min_length=1, max_length=160)]
    input_schema: JsonObject
    input_schema_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    output_schema: JsonObject
    output_schema_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    config_schema: JsonObject
    config_schema_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    receipt_schema: JsonObject
    receipt_schema_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    credential_boundary: Literal["none", "adapter_owner"]
    effect_class: EffectClass
    approval_policy: ToolApprovalPolicy
    cost: ToolCost
    readiness: ToolReadiness
    idempotency: ToolIdempotencyPolicy
    reconciliation: ToolReconciliationPolicy
    enabled: bool = True

    @property
    def execution_identity_sha256(self) -> str:
        """Bind immutable execution semantics while allowing readiness heartbeats to refresh."""
        return canonical_sha256(
            self.model_dump(mode="json", exclude={"readiness", "enabled"})
        )

    @model_validator(mode="after")
    def require_safe_effect_policy(self) -> Self:
        schema_bindings = (
            (self.input_schema, self.input_schema_sha256),
            (self.output_schema, self.output_schema_sha256),
            (self.config_schema, self.config_schema_sha256),
            (self.receipt_schema, self.receipt_schema_sha256),
        )
        if any(canonical_sha256(schema) != digest for schema, digest in schema_bindings):
            message = "tool descriptor schema digest mismatch"
            raise ValueError(message)
        if self.effect_class is EffectClass.OBSERVE and self.approval_policy.mode != "none":
            message = "observe tools cannot require effect approval"
            raise ValueError(message)
        if self.effect_class is not EffectClass.OBSERVE and self.approval_policy.mode != "required":
            message = "effect tools require approval"
            raise ValueError(message)
        if self.effect_class is EffectClass.OBSERVE and self.reconciliation.mode != "none":
            message = "observe tools do not reconcile external effects"
            raise ValueError(message)
        return self


class ToolExecutionResult(ContractModel):
    schema_version: Literal["trace.tool-execution-result.v1"]
    disposition: Literal["no_effect", "succeeded", "failed"]
    invocation_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    output: JsonObject
    actual_cost_units: Annotated[int, Field(ge=0, le=1_000_000)]
    executor_id: Annotated[str, Field(min_length=1, max_length=160)]


__all__ = [
    "EffectClass",
    "ToolApprovalPolicy",
    "ToolCost",
    "ToolDescriptor",
    "ToolExecutionResult",
    "ToolIdempotencyPolicy",
    "ToolReadiness",
    "ToolReconciliationPolicy",
]

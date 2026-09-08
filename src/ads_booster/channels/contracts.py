"""Portable contracts shared by every Marketing Agent channel adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, HttpUrl, model_validator

from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, BoundedId
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.transport.json_types import JsonObject


class ChannelKind(StrEnum):
    WEB = "web"
    SLACK = "slack"
    KAKAO = "kakao"


class ChannelInstallation(ContractModel):
    """Non-secret installation metadata; credentials remain at the adapter boundary."""

    schema_version: Literal["trace.channel-installation.v1"]
    installation_id: BoundedId
    channel: ChannelKind
    external_workspace_id: BoundedId
    tenant_id: BoundedId
    credential_reference: Annotated[str, Field(min_length=1, max_length=512)]
    enabled: bool = True
    live_verified_at: datetime | None = None
    created_at: datetime

    @model_validator(mode="after")
    def require_utc_times(self) -> Self:
        _require_utc(self.created_at)
        if self.live_verified_at is not None:
            _require_utc(self.live_verified_at)
        return self


class ChannelIdentityBinding(ContractModel):
    """Maps one channel principal onto the Agent Service tenant and member."""

    schema_version: Literal["trace.channel-identity-binding.v1"]
    binding_id: BoundedId
    installation_id: BoundedId
    external_user_id: BoundedId
    tenant_id: BoundedId
    member_id: BoundedId
    can_create_runs: bool = True
    can_approve: bool = False
    created_at: datetime
    revoked_at: datetime | None = None

    @model_validator(mode="after")
    def require_utc_times(self) -> Self:
        _require_utc(self.created_at)
        if self.revoked_at is not None:
            _require_utc(self.revoked_at)
            if self.revoked_at < self.created_at:
                raise ValueError("channel identity revocation precedes creation")
        return self


class ChannelRunRequest(ContractModel):
    schema_version: Literal["trace.channel-run-request.v1"]
    delivery_id: BoundedId
    run_id: BoundedId
    goal: AgentGoal
    budget: AgentBudget


class ChannelApprovalRequest(ContractModel):
    schema_version: Literal["trace.channel-approval-request.v1"]
    delivery_id: BoundedId
    run_id: BoundedId
    invocation_sha256: Sha256Digest
    decision: Literal["granted", "rejected"]
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def require_grant_expiry(self) -> Self:
        if self.expires_at is not None:
            _require_utc(self.expires_at)
        if self.decision == "granted" and self.expires_at is None:
            raise ValueError("channel approval grant requires expiry")
        return self


class ChannelResponse(ContractModel):
    schema_version: Literal["trace.channel-response.v1"]
    delivery_id: BoundedId
    status: Literal["accepted", "replayed", "reauth_required"]
    run_id: BoundedId
    run_state: str
    result_url: HttpUrl
    web_reauth_url: HttpUrl | None = None


class ChannelNotification(ContractModel):
    schema_version: Literal["trace.channel-notification.v1"]
    notification_id: BoundedId
    installation_id: BoundedId
    external_conversation_id: BoundedId
    run_id: BoundedId
    kind: Literal["progress", "approval_required", "result", "blocked"]
    payload: JsonObject
    created_at: datetime

    @model_validator(mode="after")
    def require_utc_time(self) -> Self:
        _require_utc(self.created_at)
        return self


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
        raise ValueError("channel contract time must be UTC")


__all__ = [
    "ChannelApprovalRequest",
    "ChannelIdentityBinding",
    "ChannelInstallation",
    "ChannelKind",
    "ChannelNotification",
    "ChannelResponse",
    "ChannelRunRequest",
]

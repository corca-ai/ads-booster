"""Shared channel-to-application translation without channel-specific agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import HttpUrl, TypeAdapter

from ads_booster.contracts.agent_run import (
    AgentRecordKind,
    AgentRunState,
    ToolApproval,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.marketing.agent_service.application import CreateAgentRunRequest
from ads_booster.marketing.channels.contracts import (
    ChannelApprovalRequest,
    ChannelIdentityBinding,
    ChannelInstallation,
    ChannelNotification,
    ChannelResponse,
    ChannelRunRequest,
)

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.marketing.agent_service.application import MarketingAgentService
    from ads_booster.marketing.channels.store import SqliteChannelStore

_HTTP_URL = TypeAdapter(HttpUrl)


@dataclass(slots=True)
class ChannelApplicationAdapter:
    """Calls the one canonical service; it contains no planning or run ownership."""

    service: MarketingAgentService
    store: SqliteChannelStore
    result_base_url: str

    def __post_init__(self) -> None:
        """Keep channel durability beside the canonical run ledger."""
        if self.store.database_path != self.service.repository.database_path:
            raise ValueError("channel store must share canonical agent database")

    def create_run(
        self,
        installation: ChannelInstallation,
        identity: ChannelIdentityBinding,
        request: ChannelRunRequest,
        *,
        now: datetime,
        notification_conversation_id: str | None = None,
    ) -> ChannelResponse:
        self._require_binding(installation, identity)
        if not identity.can_create_runs:
            raise ValueError("channel identity cannot create runs")
        admission = self.store.admit_delivery(
            installation.installation_id,
            request.delivery_id,
            request.model_dump(mode="json"),
            now=now,
        )
        if admission.response is not None:
            return admission.response.model_copy(update={"status": "replayed"})
        run = self.service.create(
            CreateAgentRunRequest(
                run_id=request.run_id,
                tenant_id=identity.tenant_id,
                goal=request.goal,
                budget=request.budget,
            ),
            now=now,
        )
        response = ChannelResponse(
            schema_version="trace.channel-response.v1",
            delivery_id=request.delivery_id,
            status="accepted",
            run_id=run.run_id,
            run_state=run.state.value,
            result_url=self.result_url(run.run_id),
        )
        self.store.complete_delivery(
            installation.installation_id,
            request.delivery_id,
            response,
            now=now,
            notification=(
                None
                if notification_conversation_id is None
                else self.notification_for_response(
                    installation,
                    conversation_id=notification_conversation_id,
                    response=response,
                    now=now,
                )
            ),
        )
        return response

    def decide_approval(
        self,
        installation: ChannelInstallation,
        identity: ChannelIdentityBinding,
        request: ChannelApprovalRequest,
        *,
        now: datetime,
        notification_conversation_id: str | None = None,
    ) -> ChannelResponse:
        self._require_binding(installation, identity)
        if not identity.can_approve:
            raise ValueError("channel identity is not a linked reviewer")
        admission = self.store.admit_delivery(
            installation.installation_id,
            request.delivery_id,
            request.model_dump(mode="json"),
            now=now,
        )
        if admission.response is not None:
            return admission.response.model_copy(update={"status": "replayed"})
        run = self.service.repository.get(identity.tenant_id, request.run_id)
        if run is None:
            raise ValueError("agent_run_not_found")
        if run.state is AgentRunState.AWAITING_APPROVAL:
            self.require_exact_pending_approval(identity, request)
            run = self.service.decide_approval(
                identity.tenant_id,
                request.run_id,
                approver_id=identity.member_id,
                granted=request.decision == "granted",
                expires_at=request.expires_at,
                now=now,
            )
        elif not self._approval_already_applied(identity, request):
            raise ValueError("channel approval cannot be reconciled")
        response = ChannelResponse(
            schema_version="trace.channel-response.v1",
            delivery_id=request.delivery_id,
            status="accepted",
            run_id=run.run_id,
            run_state=run.state.value,
            result_url=self.result_url(run.run_id),
        )
        self.store.complete_delivery(
            installation.installation_id,
            request.delivery_id,
            response,
            now=now,
            notification=(
                None
                if notification_conversation_id is None
                else self.notification_for_response(
                    installation,
                    conversation_id=notification_conversation_id,
                    response=response,
                    now=now,
                )
            ),
        )
        return response

    def require_exact_pending_approval(
        self,
        identity: ChannelIdentityBinding,
        request: ChannelApprovalRequest,
    ) -> None:
        """Bind a channel action to the one invocation awaiting service approval."""
        run = self.service.repository.get(identity.tenant_id, request.run_id)
        if run is None:
            raise ValueError("agent_run_not_found")
        if run.state is not AgentRunState.AWAITING_APPROVAL:
            raise ValueError("agent_run_not_awaiting_approval")
        invocation = self._latest_invocation(identity.tenant_id, request.run_id)
        if contract_sha256(invocation) != request.invocation_sha256:
            raise ValueError("channel approval invocation mismatch")

    def _approval_already_applied(
        self,
        identity: ChannelIdentityBinding,
        request: ChannelApprovalRequest,
    ) -> bool:
        expected_decision = request.decision
        for record in reversed(self.service.repository.records(identity.tenant_id, request.run_id)):
            if record.kind is not AgentRecordKind.APPROVAL:
                continue
            approval = ToolApproval.model_validate(record.payload)
            return (
                approval.invocation_sha256 == request.invocation_sha256
                and approval.approver_id == identity.member_id
                and approval.decision == expected_decision
            )
        return False

    def _latest_invocation(self, tenant_id: str, run_id: str) -> ToolInvocation:
        for record in reversed(self.service.repository.records(tenant_id, run_id)):
            if record.kind is AgentRecordKind.INVOCATION:
                return ToolInvocation.model_validate(record.payload)
        raise ValueError("channel approval invocation missing")

    def result_url(self, run_id: str) -> HttpUrl:
        return _HTTP_URL.validate_python(
            f"{self.result_base_url.rstrip('/')}/runs/{quote(run_id, safe='')}"
        )

    def notification_for_response(
        self,
        installation: ChannelInstallation,
        *,
        conversation_id: str,
        response: ChannelResponse,
        now: datetime,
    ) -> ChannelNotification:
        if response.run_state == AgentRunState.AWAITING_APPROVAL.value:
            kind = "approval_required"
        elif response.run_state in {
            AgentRunState.BLOCKED.value,
            AgentRunState.FAILED.value,
            AgentRunState.AWAITING_RECONCILIATION.value,
        }:
            kind = "blocked"
        elif response.run_state in {
            AgentRunState.COMPLETED.value,
            AgentRunState.STOPPED.value,
        }:
            kind = "result"
        else:
            kind = "progress"
        identity = contract_sha256(
            {
                "installation_id": installation.installation_id,
                "delivery_id": response.delivery_id,
                "run_id": response.run_id,
                "run_state": response.run_state,
            }
        )
        return ChannelNotification(
            schema_version="trace.channel-notification.v1",
            notification_id=f"channel-note-{identity[:32]}",
            installation_id=installation.installation_id,
            external_conversation_id=conversation_id,
            run_id=response.run_id,
            kind=kind,
            payload={
                "run_state": response.run_state,
                "result_url": str(response.result_url),
            },
            created_at=now,
        )

    @staticmethod
    def _require_binding(
        installation: ChannelInstallation,
        identity: ChannelIdentityBinding,
    ) -> None:
        if (
            not installation.enabled
            or identity.revoked_at is not None
            or installation.installation_id != identity.installation_id
            or installation.tenant_id != identity.tenant_id
        ):
            raise ValueError("channel identity binding invalid")


__all__ = ["ChannelApplicationAdapter"]

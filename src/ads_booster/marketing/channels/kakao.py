"""Provisioned Kakao ingress; approvals are deliberately redirected to Web re-auth."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlencode, urlsplit

from pydantic import Field, HttpUrl, TypeAdapter

from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.channels.base import ChannelApplicationAdapter
from ads_booster.marketing.channels.contracts import (
    ChannelApprovalRequest,
    ChannelKind,
    ChannelResponse,
    ChannelRunRequest,
)
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_HTTP_URL = TypeAdapter(HttpUrl)


class KakaoWebhookEnvelope(ContractModel):
    schema_version: Literal["trace.kakao-webhook.v1"]
    event_id: Annotated[str, Field(min_length=1, max_length=160)]
    bot_id: Annotated[str, Field(min_length=1, max_length=160)]
    user_id: Annotated[str, Field(min_length=1, max_length=160)]
    conversation_id: Annotated[str, Field(min_length=1, max_length=160)]
    action: Literal["create_run", "approve"]
    request: JsonObject


@dataclass(frozen=True, slots=True)
class WebApprovalLinkIssuer:
    base_url: str
    signing_secret: bytes

    def __post_init__(self) -> None:
        """Reject approval links that could disclose state over cleartext."""
        parts = urlsplit(self.base_url)
        if parts.scheme != "https" and parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("approval reauth URL must use HTTPS or loopback")

    def issue(
        self,
        *,
        tenant_id: str,
        member_id: str,
        request: ChannelApprovalRequest,
        expires_at: datetime,
    ) -> HttpUrl:
        if expires_at.tzinfo is None or expires_at.utcoffset() != UTC.utcoffset(None):
            raise ValueError("approval reauth expiry must be UTC")
        fields = {
            "decision": request.decision,
            "expires": str(int(expires_at.timestamp())),
            "invocation": request.invocation_sha256,
            "member": member_id,
            "run": request.run_id,
            "tenant": tenant_id,
        }
        material = urlencode(fields)
        fields["signature"] = hmac.new(
            self.signing_secret, material.encode(), hashlib.sha256
        ).hexdigest()
        return _HTTP_URL.validate_python(
            f"{self.base_url.rstrip('/')}/approvals/reauth?{urlencode(fields)}"
        )


@dataclass(slots=True)
class KakaoChannelAdapter:
    application: ChannelApplicationAdapter
    provisioned_secret: bytes
    link_issuer: WebApprovalLinkIssuer

    def handle(
        self,
        raw_body: bytes,
        *,
        secret_header: str | None,
        now: datetime,
    ) -> ChannelResponse:
        if secret_header is None or not hmac.compare_digest(
            secret_header.encode(), self.provisioned_secret
        ):
            raise ValueError("kakao provisioned secret invalid")
        envelope = KakaoWebhookEnvelope.model_validate(_JSON_OBJECT.validate_json(raw_body))
        installation = self.application.store.resolve_installation(
            ChannelKind.KAKAO, envelope.bot_id
        )
        identity = self.application.store.resolve_identity(
            installation.installation_id, envelope.user_id
        )
        payload = {**envelope.request, "delivery_id": envelope.event_id}
        if envelope.action == "create_run":
            return self.application.create_run(
                installation,
                identity,
                ChannelRunRequest.model_validate(payload),
                now=now,
                notification_conversation_id=envelope.conversation_id,
            )
        request = ChannelApprovalRequest.model_validate(payload)
        if not identity.can_approve:
            raise ValueError("channel identity is not a linked reviewer")
        admission = self.application.store.admit_delivery(
            installation.installation_id,
            request.delivery_id,
            request.model_dump(mode="json"),
            now=now,
        )
        if admission.response is not None:
            return admission.response.model_copy(update={"status": "replayed"})
        self.application.require_exact_pending_approval(identity, request)
        expiry = request.expires_at or now + timedelta(minutes=5)
        run = self.application.service.repository.get(identity.tenant_id, request.run_id)
        if run is None:
            raise ValueError("agent_run_not_found")
        response = ChannelResponse(
            schema_version="trace.channel-response.v1",
            delivery_id=request.delivery_id,
            status="reauth_required",
            run_id=run.run_id,
            run_state=run.state.value,
            result_url=self.application.result_url(run.run_id),
            web_reauth_url=self.link_issuer.issue(
                tenant_id=identity.tenant_id,
                member_id=identity.member_id,
                request=request,
                expires_at=expiry,
            ),
        )
        self.application.store.complete_delivery(
            installation.installation_id,
            request.delivery_id,
            response,
            now=now,
            notification=self.application.notification_for_response(
                installation,
                conversation_id=envelope.conversation_id,
                response=response,
                now=now,
            ),
        )
        return response


__all__ = ["KakaoChannelAdapter", "KakaoWebhookEnvelope", "WebApprovalLinkIssuer"]

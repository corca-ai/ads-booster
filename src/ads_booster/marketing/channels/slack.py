"""Slack ingress authentication and identity-bound channel translation."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.channels.base import ChannelApplicationAdapter
from ads_booster.marketing.channels.contracts import (
    ChannelApprovalRequest,
    ChannelKind,
    ChannelResponse,
    ChannelRunRequest,
)
from ads_booster.transport.json_types import JsonObject

_MAX_CLOCK_SKEW_SECONDS = 300
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class SlackWebhookEnvelope(ContractModel):
    schema_version: Literal["trace.slack-webhook.v1"]
    event_id: Annotated[str, Field(min_length=1, max_length=160)]
    team_id: Annotated[str, Field(min_length=1, max_length=160)]
    user_id: Annotated[str, Field(min_length=1, max_length=160)]
    conversation_id: Annotated[str, Field(min_length=1, max_length=160)]
    action: Literal["create_run", "approve"]
    request: JsonObject


@dataclass(frozen=True, slots=True)
class SlackRequestVerifier:
    signing_secret: bytes

    def verify(self, raw_body: bytes, *, timestamp: str, signature: str, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(None):
            raise ValueError("slack verification time must be UTC")
        try:
            signed_at = int(timestamp)
        except ValueError as error:
            raise ValueError("slack timestamp invalid") from error
        if abs(int(now.timestamp()) - signed_at) > _MAX_CLOCK_SKEW_SECONDS:
            raise ValueError("slack request stale")
        base = b"v0:" + timestamp.encode("ascii") + b":" + raw_body
        expected = "v0=" + hmac.new(self.signing_secret, base, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("slack signature invalid")


@dataclass(slots=True)
class SlackChannelAdapter:
    application: ChannelApplicationAdapter
    verifier: SlackRequestVerifier

    def handle(
        self,
        raw_body: bytes,
        *,
        timestamp: str,
        signature: str,
        now: datetime,
    ) -> ChannelResponse:
        self.verifier.verify(raw_body, timestamp=timestamp, signature=signature, now=now)
        envelope = SlackWebhookEnvelope.model_validate(_JSON_OBJECT.validate_json(raw_body))
        installation = self.application.store.resolve_installation(
            ChannelKind.SLACK, envelope.team_id
        )
        identity = self.application.store.resolve_identity(
            installation.installation_id, envelope.user_id
        )
        payload = {**envelope.request, "delivery_id": envelope.event_id}
        if envelope.action == "create_run":
            response = self.application.create_run(
                installation,
                identity,
                ChannelRunRequest.model_validate(payload),
                now=now,
                notification_conversation_id=envelope.conversation_id,
            )
        else:
            response = self.application.decide_approval(
                installation,
                identity,
                ChannelApprovalRequest.model_validate(payload),
                now=now,
                notification_conversation_id=envelope.conversation_id,
            )
        return response


def slack_signature(secret: bytes, raw_body: bytes, timestamp: str) -> str:
    """Build a signature for contract tests; it is not live Slack verification evidence."""
    base = b"v0:" + timestamp.encode("ascii") + b":" + raw_body
    return "v0=" + hmac.new(secret, base, hashlib.sha256).hexdigest()


def encode_slack_envelope(envelope: SlackWebhookEnvelope) -> bytes:
    return json.dumps(
        envelope.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


__all__ = [
    "SlackChannelAdapter",
    "SlackRequestVerifier",
    "SlackWebhookEnvelope",
    "encode_slack_envelope",
    "slack_signature",
]

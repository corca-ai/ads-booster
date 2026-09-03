from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRecordKind,
    AgentRunState,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.contracts.tool_capability import (
    EffectClass,
    ToolApprovalPolicy,
    ToolCost,
    ToolDescriptor,
    ToolExecutionResult,
    ToolIdempotencyPolicy,
    ToolReadiness,
    ToolReconciliationPolicy,
)
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.channels.base import ChannelApplicationAdapter
from ads_booster.marketing.channels.contracts import (
    ChannelApprovalRequest,
    ChannelIdentityBinding,
    ChannelInstallation,
    ChannelKind,
    ChannelRunRequest,
)
from ads_booster.marketing.channels.kakao import (
    KakaoChannelAdapter,
    KakaoWebhookEnvelope,
    WebApprovalLinkIssuer,
)
from ads_booster.marketing.channels.slack import (
    SlackChannelAdapter,
    SlackRequestVerifier,
    SlackWebhookEnvelope,
    encode_slack_envelope,
    slack_signature,
)
from ads_booster.marketing.channels.store import SqliteChannelStore
from ads_booster.marketing.channels.web import WebChannelAdapter
from ads_booster.marketing.runtime import SqliteSessionStore

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)
SLACK_SECRET = b"fake-slack-signing-secret"
KAKAO_SECRET = b"fake-kakao-provisioned-secret"


class StopReasoning:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="stop",
            expected_outcome="Record a bounded channel request",
            reasoning_summary="No tool is needed for the fake channel contract test",
        )
        return _reasoning_result(request, decision)


class ApprovalReasoning:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        if request.evidence:
            decision = ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="stop",
                expected_outcome="The approved effect was recorded",
                reasoning_summary="The bounded effect completed",
            )
        else:
            decision = ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id="creative.render",
                tool_input={"concept": "changing lock screen"},
                expected_outcome="Render an approved asset",
                reasoning_summary="A visual asset is the next bounded experiment",
            )
        return _reasoning_result(request, decision)


class FakeEffectAdapter:
    def __init__(self) -> None:
        self.calls: list[JsonObject] = []

    def execute(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> ToolExecutionResult:
        assert invocation.descriptor_sha256 == contract_sha256(descriptor)
        self.calls.append(invocation.input)
        return ToolExecutionResult(
            schema_version="trace.tool-execution-result.v1",
            disposition="succeeded",
            invocation_sha256=contract_sha256(invocation),
            output={"artifact_id": "fake-artifact"},
            actual_cost_units=1,
            executor_id="fake.effect",
        )


def test_web_slack_and_kakao_create_runs_through_one_service(tmp_path: Path) -> None:
    application, store = _application(tmp_path, StopReasoning())
    web_installation, web_identity = _provision(store, ChannelKind.WEB, "web-workspace")
    slack_installation, _ = _provision(store, ChannelKind.SLACK, "team-one")
    kakao_installation, _ = _provision(store, ChannelKind.KAKAO, "bot-one")

    web = WebChannelAdapter(application)
    slack = SlackChannelAdapter(application, SlackRequestVerifier(SLACK_SECRET))
    kakao = KakaoChannelAdapter(
        application,
        KAKAO_SECRET,
        WebApprovalLinkIssuer("https://agent.example", b"fake-link-secret"),
    )

    web_response = web.create_run(
        web_installation,
        web_identity,
        _run_request("web-delivery", "web-run"),
        now=NOW,
    )
    slack_body = encode_slack_envelope(
        SlackWebhookEnvelope(
            schema_version="trace.slack-webhook.v1",
            event_id="slack-delivery",
            team_id=slack_installation.external_workspace_id,
            user_id="external-user",
            conversation_id="channel-one",
            action="create_run",
            request=_without_delivery(_run_request("ignored", "slack-run")),
        )
    )
    timestamp = str(int(NOW.timestamp()))
    slack_response = slack.handle(
        slack_body,
        timestamp=timestamp,
        signature=slack_signature(SLACK_SECRET, slack_body, timestamp),
        now=NOW,
    )
    kakao_body = _kakao_body(
        KakaoWebhookEnvelope(
            schema_version="trace.kakao-webhook.v1",
            event_id="kakao-delivery",
            bot_id=kakao_installation.external_workspace_id,
            user_id="external-user",
            conversation_id="room-one",
            action="create_run",
            request=_without_delivery(_run_request("ignored", "kakao-run")),
        )
    )
    kakao_response = kakao.handle(
        kakao_body,
        secret_header=KAKAO_SECRET.decode(),
        now=NOW,
    )

    assert {web_response.run_id, slack_response.run_id, kakao_response.run_id} == {
        "web-run",
        "slack-run",
        "kakao-run",
    }
    assert all(
        application.service.repository.get("trace", run_id) is not None
        for run_id in ("web-run", "slack-run", "kakao-run")
    )


def test_slack_requires_raw_body_signature_freshness_and_dedupes(tmp_path: Path) -> None:
    application, store = _application(tmp_path, StopReasoning())
    installation, _ = _provision(store, ChannelKind.SLACK, "team-one")
    adapter = SlackChannelAdapter(application, SlackRequestVerifier(SLACK_SECRET))
    body = encode_slack_envelope(
        SlackWebhookEnvelope(
            schema_version="trace.slack-webhook.v1",
            event_id="event-one",
            team_id=installation.external_workspace_id,
            user_id="external-user",
            conversation_id="channel-one",
            action="create_run",
            request=_without_delivery(_run_request("ignored", "same-run")),
        )
    )
    timestamp = str(int(NOW.timestamp()))
    signature = slack_signature(SLACK_SECRET, body, timestamp)

    first = adapter.handle(body, timestamp=timestamp, signature=signature, now=NOW)
    replay = adapter.handle(body, timestamp=timestamp, signature=signature, now=NOW)

    assert first.status == "accepted"
    assert replay.status == "replayed"
    with pytest.raises(ValueError, match="slack signature invalid"):
        _ = adapter.handle(body + b" ", timestamp=timestamp, signature=signature, now=NOW)
    with pytest.raises(ValueError, match="slack request stale"):
        _ = adapter.handle(
            body,
            timestamp=timestamp,
            signature=signature,
            now=NOW + timedelta(seconds=301),
        )


def test_slack_linked_reviewer_exact_approves_pending_invocation(tmp_path: Path) -> None:
    effect = FakeEffectAdapter()
    application, store = _application(tmp_path, ApprovalReasoning(), effect=effect)
    installation, _ = _provision(
        store,
        ChannelKind.SLACK,
        "team-one",
        can_approve=True,
    )
    waiting = application.service.create(
        _service_request("approval-run"),
        now=NOW,
    )
    invocation_sha256 = _invocation_sha256(application, waiting.run_id)
    envelope = SlackWebhookEnvelope(
        schema_version="trace.slack-webhook.v1",
        event_id="approval-event",
        team_id=installation.external_workspace_id,
        user_id="external-user",
        conversation_id="channel-one",
        action="approve",
        request=ChannelApprovalRequest(
            schema_version="trace.channel-approval-request.v1",
            delivery_id="ignored",
            run_id=waiting.run_id,
            invocation_sha256=invocation_sha256,
            decision="granted",
            expires_at=NOW + timedelta(minutes=5),
        ).model_dump(mode="json", exclude={"delivery_id"}),
    )
    body = encode_slack_envelope(envelope)
    timestamp = str(int(NOW.timestamp()))

    response = SlackChannelAdapter(application, SlackRequestVerifier(SLACK_SECRET)).handle(
        body,
        timestamp=timestamp,
        signature=slack_signature(SLACK_SECRET, body, timestamp),
        now=NOW + timedelta(seconds=1),
    )

    assert response.run_state == AgentRunState.COMPLETED.value
    assert effect.calls == [{"concept": "changing lock screen"}]
    approval_records = [
        record
        for record in application.service.repository.records("trace", waiting.run_id)
        if record.kind is AgentRecordKind.APPROVAL
    ]
    assert len(approval_records) == 1
    assert approval_records[0].payload["approver_id"] == "member-one"


def test_kakao_approval_returns_web_reauth_without_mutating_run(tmp_path: Path) -> None:
    effect = FakeEffectAdapter()
    application, store = _application(tmp_path, ApprovalReasoning(), effect=effect)
    installation, _ = _provision(
        store,
        ChannelKind.KAKAO,
        "bot-one",
        can_approve=True,
    )
    waiting = application.service.create(_service_request("kakao-approval-run"), now=NOW)
    before_revision = waiting.revision
    request = ChannelApprovalRequest(
        schema_version="trace.channel-approval-request.v1",
        delivery_id="ignored",
        run_id=waiting.run_id,
        invocation_sha256=_invocation_sha256(application, waiting.run_id),
        decision="granted",
        expires_at=NOW + timedelta(minutes=5),
    )
    body = _kakao_body(
        KakaoWebhookEnvelope(
            schema_version="trace.kakao-webhook.v1",
            event_id="kakao-approval-event",
            bot_id=installation.external_workspace_id,
            user_id="external-user",
            conversation_id="room-one",
            action="approve",
            request=request.model_dump(mode="json", exclude={"delivery_id"}),
        )
    )
    adapter = KakaoChannelAdapter(
        application,
        KAKAO_SECRET,
        WebApprovalLinkIssuer("https://agent.example", b"fake-link-secret"),
    )

    response = adapter.handle(body, secret_header=KAKAO_SECRET.decode(), now=NOW)

    persisted = application.service.repository.get("trace", waiting.run_id)
    assert response.status == "reauth_required"
    assert response.web_reauth_url is not None
    assert str(response.web_reauth_url).startswith("https://agent.example/approvals/reauth?")
    assert persisted is not None
    assert persisted.revision == before_revision
    assert persisted.state is AgentRunState.AWAITING_APPROVAL
    assert effect.calls == []
    with pytest.raises(ValueError, match="kakao provisioned secret invalid"):
        _ = adapter.handle(body, secret_header="wrong", now=NOW)  # noqa: S106


def _application(
    root: Path,
    reasoning: StopReasoning | ApprovalReasoning,
    *,
    effect: FakeEffectAdapter | None = None,
) -> tuple[ChannelApplicationAdapter, SqliteChannelStore]:
    database = root / "agent.sqlite3"
    descriptors = () if effect is None else (_effect_descriptor(),)
    tools = {} if effect is None else {"creative.render": effect}
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(database),
        registry=ToolRegistry(descriptors),
        reasoning=reasoning,
        tools=tools,
        runtime_store=SqliteSessionStore(database),
    )
    store = SqliteChannelStore(database)
    return ChannelApplicationAdapter(service, store, "https://agent.example"), store


def _provision(
    store: SqliteChannelStore,
    channel: ChannelKind,
    workspace_id: str,
    *,
    can_approve: bool = False,
) -> tuple[ChannelInstallation, ChannelIdentityBinding]:
    installation = ChannelInstallation(
        schema_version="trace.channel-installation.v1",
        installation_id=f"{channel.value}-installation",
        channel=channel,
        external_workspace_id=workspace_id,
        tenant_id="trace",
        credential_reference=f"env:FAKE_{channel.value.upper()}_SECRET",
        created_at=NOW,
    )
    identity = ChannelIdentityBinding(
        schema_version="trace.channel-identity-binding.v1",
        binding_id=f"{channel.value}-binding",
        installation_id=installation.installation_id,
        external_user_id="external-user",
        tenant_id="trace",
        member_id="member-one",
        can_approve=can_approve,
        created_at=NOW,
    )
    store.put_installation(installation)
    store.put_identity(identity)
    return installation, identity


def _run_request(delivery_id: str, run_id: str) -> ChannelRunRequest:
    return ChannelRunRequest(
        schema_version="trace.channel-run-request.v1",
        delivery_id=delivery_id,
        run_id=run_id,
        goal=AgentGoal(
            objective="Market the changing AI lock screen",
            success_criteria=("record one experiment",),
        ),
        budget=AgentBudget(max_tool_calls=2, max_cost_units=4),
    )


def _service_request(run_id: str) -> CreateAgentRunRequest:
    request = _run_request("direct", run_id)
    return CreateAgentRunRequest(
        run_id=request.run_id,
        tenant_id="trace",
        goal=request.goal,
        budget=request.budget,
    )


def _without_delivery(request: ChannelRunRequest) -> JsonObject:
    return request.model_dump(mode="json", exclude={"delivery_id"})


def _kakao_body(envelope: KakaoWebhookEnvelope) -> bytes:
    return json.dumps(
        envelope.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _invocation_sha256(application: ChannelApplicationAdapter, run_id: str) -> str:
    record = next(
        record
        for record in reversed(application.service.repository.records("trace", run_id))
        if record.kind is AgentRecordKind.INVOCATION
    )
    return contract_sha256(ToolInvocation.model_validate(record.payload))


def _effect_descriptor() -> ToolDescriptor:
    schema: JsonObject = {"type": "object"}
    digest = contract_sha256(schema)
    return ToolDescriptor(
        schema_version="trace.tool-descriptor.v1",
        capability_id="creative.render",
        version="1",
        owner="fake.effect",
        installation_id="fake-effect-installation",
        input_schema=schema,
        input_schema_sha256=digest,
        output_schema=schema,
        output_schema_sha256=digest,
        config_schema=schema,
        config_schema_sha256=digest,
        receipt_schema=schema,
        receipt_schema_sha256=digest,
        credential_boundary="none",
        effect_class=EffectClass.LOCAL_ARTIFACT,
        approval_policy=ToolApprovalPolicy(mode="required"),
        cost=ToolCost(worst_case_units=1, unit="operation"),
        readiness=ToolReadiness(
            ready=True,
            observed_at=NOW,
            max_age_seconds=300,
        ),
        idempotency=ToolIdempotencyPolicy(key_scope="run_tool_input"),
        reconciliation=ToolReconciliationPolicy(
            mode="readback",
            lookup_capability_id="creative.status",
            terminal_dispositions=("succeeded", "failed"),
        ),
    )


def _reasoning_result(
    request: ReasoningRequest,
    decision: ReasoningDecision,
) -> ReasoningResult:
    return ReasoningResult(
        schema_version="trace.reasoning-result.v1",
        decision=decision,
        receipt=ReasoningProviderReceipt(
            schema_version="trace.reasoning-provider-receipt.v1",
            provider_id="fake.reasoning",
            model_id="fake-model",
            request_sha256=contract_sha256(request),
            output_schema_sha256="f" * 64,
            decision_sha256=contract_sha256(decision),
        ),
    )

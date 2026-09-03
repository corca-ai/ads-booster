"""Canonical bridge from dynamic research into the existing hosted marketing control plane."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter, model_validator

from ads_booster.contracts.marketing_agent import (
    AgentIdentifier,
    FeatureEvidencePacket,
    FeatureLaunchLineage,
    contract_sha256,
)
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.dynamic_evidence_research import (
    DynamicEvidenceResearchRequest,
    DynamicEvidenceResearchResult,
    DynamicEvidenceResearchRunner,
    ResearchContinuation,
)
from ads_booster.marketing.evidence_research_operator import ResearchScope
from ads_booster.marketing.runtime import (
    AgentSession,
    BoundToolInvocation,
    Budget,
    EffectDisposition,
    JsonSessionStore,
    MarketingAgentRuntime,
    RuntimeState,
    ToolAdmission,
    ToolCapability,
    ToolReceipt,
    bind_tool_invocation,
    canonical_json_object,
    canonical_json_sha256,
)
from ads_booster.transport.http import HttpClient
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_HANDOFF_CAPABILITY_ID = "control_plane.create_shadow_campaign"
_HANDOFF_SCHEMA_VERSION = "trace.hosted-campaign-handoff.v1"
_LINEAGE_SCHEMA_VERSION = "trace.feature-launch-lineage.v1"
_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300
_HTTP_NOT_FOUND = 404
_HTTP_CONFLICT = 409
_HTTP_PROVEN_NO_EFFECT = frozenset({400, 401, 403, 404, 413, 422, 503})
_MAX_HOSTED_HANDOFF_BYTES = 64 * 1024
_HOSTED_ACCOUNT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class FeatureLaunchRunError(ValueError):
    """The research-to-control-plane bridge could not prove a safe transition."""


class FeatureLaunchRunRequest(ContractModel):
    schema_version: Literal["trace.feature-launch-run-request.v1"]
    agent_run_id: AgentIdentifier
    research: DynamicEvidenceResearchRequest
    business_outcome: Annotated[str, Field(min_length=1, max_length=1000)]
    current_control: Annotated[str, Field(min_length=1, max_length=4000)]
    marketing_context_snapshot_id: AgentIdentifier | None = None

    @model_validator(mode="after")
    def require_shadow_research_contract(self) -> FeatureLaunchRunRequest:
        if _HOSTED_ACCOUNT_ID.fullmatch(self.research.account_id) is None:
            raise ValueError("hosted account_id must use lowercase letters, numbers, - or _")
        if self.research.feature_packet.gate.publication_allowed:
            raise ValueError("feature launch bridge currently admits shadow campaigns only")
        required = set(self.research.required_scopes)
        if not {ResearchScope.PRODUCT_TRUTH, ResearchScope.MARKET_EVIDENCE}.issubset(required):
            raise ValueError("feature launch bridge requires product truth and market evidence")
        if self.research.market_context is None:
            raise ValueError("feature launch bridge requires a market objective")
        if (
            self.research.market_context.business_outcome != self.business_outcome
            or self.research.market_context.current_control != self.current_control
        ):
            raise ValueError("feature launch objective must match its frozen research snapshot")
        context = self.research.marketing_context
        if context is not None and self.marketing_context_snapshot_id != context.snapshot_id:
            raise ValueError("hosted marketing context must match the frozen research snapshot")
        if context is None and self.marketing_context_snapshot_id is not None:
            raise ValueError("hosted marketing context has no matching research projection")
        _validate_hosted_string(self.business_outcome, field="business_outcome", maximum=1000)
        _validate_hosted_string(self.current_control, field="current_control", maximum=4000)
        _validate_hosted_packet_projection(self.research.feature_packet)
        return self


class HostedCampaignHandoff(ContractModel):
    schema_version: Literal["trace.hosted-campaign-handoff.v1"]
    account_id: AgentIdentifier
    campaign_id: AgentIdentifier
    business_outcome: Annotated[str, Field(min_length=1, max_length=1000)]
    current_control: Annotated[str, Field(min_length=1, max_length=4000)]
    feature_packet: JsonObject
    marketing_context_snapshot_id: AgentIdentifier | None = None
    research_enabled: Literal[True]
    mode: Literal["shadow"]
    agent_run_lineage: FeatureLaunchLineage


class FeatureLaunchRunResult(ContractModel):
    schema_version: Literal["trace.feature-launch-run-result.v1"]
    agent_run_id: AgentIdentifier
    state: Literal["created", "blocked", "awaiting_reconciliation"]
    research: DynamicEvidenceResearchResult
    lineage: FeatureLaunchLineage | None = None
    campaign_status: JsonObject | None = None


class HostedCampaignControlPlane(Protocol):
    def execute(self, invocation: BoundToolInvocation) -> ToolReceipt: ...

    def lookup(self, campaign_id: str, account_id: str) -> JsonObject | None: ...


@dataclass(frozen=True, slots=True)
class HttpHostedCampaignControlPlane:
    http: HttpClient
    origin: str
    bearer_token: str = field(repr=False)

    def execute(self, invocation: BoundToolInvocation) -> ToolReceipt:
        invocation.validate()
        handoff = HostedCampaignHandoff.model_validate(invocation.request)
        payload = _handoff_payload(handoff)
        _validate_handoff_size(payload)
        response = self.http.post_json(
            f"{self.origin}/api/marketing-agent/campaigns",
            payload,
            self._headers(handoff.account_id),
        )
        if _HTTP_SUCCESS_MIN <= response.status_code < _HTTP_SUCCESS_MAX:
            status = response.json_object()
            _validate_hosted_status(status, handoff)
            return _receipt(invocation, EffectDisposition.SUCCEEDED, status)
        if response.status_code == _HTTP_CONFLICT:
            status = self.lookup(handoff.campaign_id, handoff.account_id)
            if status is not None:
                _validate_hosted_status(status, handoff)
                return _receipt(invocation, EffectDisposition.SUCCEEDED, status)
        if response.status_code in _HTTP_PROVEN_NO_EFFECT:
            return _receipt(
                invocation,
                EffectDisposition.FAILED,
                {
                    "http_status": response.status_code,
                    "body_sha256": sha256(response.content).hexdigest(),
                },
            )
        raise FeatureLaunchRunError("hosted_campaign_create_outcome_ambiguous")

    def lookup(self, campaign_id: str, account_id: str) -> JsonObject | None:
        response = self.http.get(
            f"{self.origin}/api/marketing-agent/campaigns/{campaign_id}",
            self._headers(account_id),
        )
        if response.status_code == _HTTP_NOT_FOUND:
            return None
        if not _HTTP_SUCCESS_MIN <= response.status_code < _HTTP_SUCCESS_MAX:
            raise FeatureLaunchRunError("hosted_campaign_lookup_failed")
        return response.json_object()

    def _headers(self, account_id: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.bearer_token}",
            "content-type": "application/json",
            "x-trace-account-id": account_id,
        }


@dataclass(frozen=True, slots=True)
class FeatureLaunchRunner:
    research_runner: DynamicEvidenceResearchRunner
    control_plane: HostedCampaignControlPlane
    state_root: Path

    def run(
        self,
        request: FeatureLaunchRunRequest,
        *,
        now: datetime | None = None,
    ) -> FeatureLaunchRunResult:
        current_time = datetime.now(UTC) if now is None else now
        if current_time.tzinfo is None or current_time.utcoffset() != UTC.utcoffset(current_time):
            raise FeatureLaunchRunError("feature_launch_now_must_be_utc")
        _validate_handoff_size(_handoff_payload(_handoff(request, _preflight_lineage(request))))
        research = self.research_runner.run(request.research, now=current_time)
        continuation = research.continuation
        if continuation is None or not _continuation_matches(request, research, continuation):
            return FeatureLaunchRunResult(
                schema_version="trace.feature-launch-run-result.v1",
                agent_run_id=request.agent_run_id,
                state="blocked",
                research=research,
            )
        lineage = _lineage(request, continuation)
        handoff = _handoff(request, lineage)
        capability = _handoff_capability()
        invocation = bind_tool_invocation(
            capability,
            call_id=f"create-shadow-{request.agent_run_id}",
            idempotency_key=f"feature-launch:{request.research.account_id}:{request.agent_run_id}",
            request=_JSON_OBJECT.validate_python(handoff.model_dump(mode="json")),
        )
        store = JsonSessionStore(Path(self.state_root) / "sessions")
        runtime = MarketingAgentRuntime()
        session_id = f"launch-{request.agent_run_id}"
        handoff_runtime = _HandoffRuntime(runtime, store, self.control_plane, current_time)
        session = handoff_runtime.dispatch_or_recover(
            store.load(session_id),
            session_id=session_id,
            capability=capability,
            invocation=invocation,
        )
        session, pending_status = handoff_runtime.resolve_ambiguous(
            session,
            handoff=handoff,
        )
        if session.state is RuntimeState.AWAITING_RECONCILIATION:
            return _launch_result(
                request,
                research,
                lineage,
                "awaiting_reconciliation",
                pending_status,
            )
        status = self.control_plane.lookup(request.agent_run_id, request.research.account_id)
        if session.state is RuntimeState.EXECUTING and session.pending_call is None:
            successful = any(event.event_type == "tool_succeeded" for event in session.events)
            session = runtime.finalize_persisted_session(
                store,
                session,
                state=RuntimeState.COMPLETED if successful else RuntimeState.INCONCLUSIVE,
                reason="hosted_campaign_created"
                if successful
                else "hosted_campaign_create_rejected",
                now=current_time,
            )
        if session.state is RuntimeState.COMPLETED:
            if status is None:
                raise FeatureLaunchRunError("persisted_hosted_campaign_missing")
            _validate_hosted_status(status, handoff)
            return _launch_result(request, research, lineage, "created", status)
        return _launch_result(request, research, lineage, "blocked", status)


@dataclass(frozen=True, slots=True)
class _HandoffRuntime:
    runtime: MarketingAgentRuntime
    store: JsonSessionStore
    control_plane: HostedCampaignControlPlane
    now: datetime

    def dispatch_or_recover(
        self,
        session: AgentSession | None,
        *,
        session_id: str,
        capability: ToolCapability,
        invocation: BoundToolInvocation,
    ) -> AgentSession:
        if session is None:
            created = AgentSession(session_id, Budget(max_tool_calls=1, max_cost_units=1))
            admitted = self.runtime.request_persisted_tool(
                self.store,
                created,
                ToolAdmission(capability, invocation),
                now=self.now,
            )
            return self.runtime.execute_persisted_tool(
                self.store,
                admitted,
                self.control_plane,
                now=self.now,
            )
        mismatched = (
            session.pending_invocation is not None and session.pending_invocation != invocation
        ) or (session.pending_invocation is None and not _session_bound_to(session, invocation))
        if mismatched:
            raise FeatureLaunchRunError("feature_launch_persisted_handoff_mismatch")
        if session.state is RuntimeState.EXECUTING and session.execution_started:
            return self.runtime.reconcile_interrupted_execution(
                self.store,
                session,
                now=self.now,
            )
        return session

    def resolve_ambiguous(
        self,
        session: AgentSession,
        *,
        handoff: HostedCampaignHandoff,
    ) -> tuple[AgentSession, JsonObject | None]:
        if session.state is not RuntimeState.AWAITING_RECONCILIATION:
            return session, None
        status = self.control_plane.lookup(handoff.campaign_id, handoff.account_id)
        if status is None:
            return session, None
        _validate_hosted_status(status, handoff)
        resolved = self.runtime.resolve_persisted_reconciliation(
            self.store,
            session,
            _receipt(session.pending_invocation, EffectDisposition.SUCCEEDED, status),
            now=self.now,
        )
        return resolved, status


def _continuation_matches(
    request: FeatureLaunchRunRequest,
    research: DynamicEvidenceResearchResult,
    continuation: ResearchContinuation,
) -> bool:
    return (
        research.state == "inconclusive"
        and continuation.account_id == request.research.account_id
        and continuation.feature_packet_id == request.research.feature_packet.packet_id
        and continuation.feature_packet_sha256 == contract_sha256(request.research.feature_packet)
        and continuation.research_session_id == request.research.session_id
        and continuation.research_input_sha256 == contract_sha256(request.research)
        and continuation.research_trace_sha256 == research.trace_sha256
        and continuation.pending_scope is ResearchScope.MARKET_EVIDENCE
    )


def _session_bound_to(session: AgentSession, invocation: BoundToolInvocation) -> bool:
    dispatches = tuple(event for event in session.events if event.event_type == "tool_dispatched")
    if len(dispatches) != 1:
        return False
    payload = dispatches[0].payload.get("invocation")
    if not isinstance(payload, dict):
        return False
    return canonical_json_object(_JSON_OBJECT.validate_python(payload)) == canonical_json_object(
        {
            "schema_version": invocation.schema_version,
            "call": {
                "call_id": invocation.call.call_id,
                "idempotency_key": invocation.call.idempotency_key,
                "capability_id": invocation.call.capability_id,
                "descriptor_sha256": invocation.call.descriptor_sha256,
                "request_schema_sha256": invocation.call.request_schema_sha256,
                "input_sha256": invocation.call.input_sha256,
                "effect_class": invocation.call.effect_class,
            },
            "request": invocation.request,
        }
    )


def _lineage(
    request: FeatureLaunchRunRequest,
    continuation: ResearchContinuation,
) -> FeatureLaunchLineage:
    return FeatureLaunchLineage(
        schema_version=_LINEAGE_SCHEMA_VERSION,
        agent_run_id=request.agent_run_id,
        research_session_id=continuation.research_session_id,
        research_input_sha256=continuation.research_input_sha256,
        research_trace_sha256=continuation.research_trace_sha256,
        research_continuation_sha256=contract_sha256(continuation),
    )


def _preflight_lineage(request: FeatureLaunchRunRequest) -> FeatureLaunchLineage:
    placeholder_sha256 = "0" * 64
    return FeatureLaunchLineage(
        schema_version=_LINEAGE_SCHEMA_VERSION,
        agent_run_id=request.agent_run_id,
        research_session_id=request.research.session_id,
        research_input_sha256=placeholder_sha256,
        research_trace_sha256=placeholder_sha256,
        research_continuation_sha256=placeholder_sha256,
    )


def _handoff(
    request: FeatureLaunchRunRequest, lineage: FeatureLaunchLineage
) -> HostedCampaignHandoff:
    return HostedCampaignHandoff(
        schema_version=_HANDOFF_SCHEMA_VERSION,
        account_id=request.research.account_id,
        campaign_id=request.agent_run_id,
        business_outcome=request.business_outcome,
        current_control=request.current_control,
        feature_packet=_JSON_OBJECT.validate_python(
            request.research.feature_packet.model_dump(mode="json")
        ),
        marketing_context_snapshot_id=request.marketing_context_snapshot_id,
        research_enabled=True,
        mode="shadow",
        agent_run_lineage=lineage,
    )


def _handoff_payload(handoff: HostedCampaignHandoff) -> JsonObject:
    return _JSON_OBJECT.validate_python(handoff.model_dump(mode="json", exclude={"schema_version"}))


def _validate_handoff_size(payload: JsonObject) -> None:
    if len(canonical_json_object(payload).encode("utf-8")) > _MAX_HOSTED_HANDOFF_BYTES:
        raise FeatureLaunchRunError("hosted_campaign_handoff_too_large")


def _validate_hosted_packet_projection(request_packet: FeatureEvidencePacket) -> None:
    # The bridge keeps the product contract unchanged while refusing values that the hosted
    # normalizer would trim or truncate before computing its packet digest.
    _validate_hosted_string(request_packet.title, field="feature_packet.title", maximum=200)
    _validate_hosted_string(
        request_packet.repository, field="feature_packet.repository", maximum=300
    )
    _validate_hosted_string(
        request_packet.mutable_ref, field="feature_packet.mutable_ref", maximum=300
    )
    for claim in request_packet.claims:
        _validate_hosted_string(claim.text, field="feature_packet.claim.text", maximum=2000)
    for evidence in request_packet.evidence:
        _validate_hosted_string(
            evidence.source_uri, field="feature_packet.evidence.source_uri", maximum=2000
        )
        _validate_hosted_string(
            evidence.immutable_ref,
            field="feature_packet.evidence.immutable_ref",
            maximum=500,
        )
    for limitation in request_packet.limitations:
        _validate_hosted_string(limitation, field="feature_packet.limitation", maximum=2000)
    for reason in request_packet.gate.reasons:
        _validate_hosted_string(reason, field="feature_packet.gate.reason", maximum=2000)


def _validate_hosted_string(value: str, *, field: str, maximum: int) -> None:
    javascript_length = len(value.encode("utf-16-le")) // 2
    if value != value.strip() or javascript_length > maximum:
        raise ValueError(f"{field} must already match the hosted canonical string")


def _handoff_capability() -> ToolCapability:
    request_schema_sha256 = canonical_json_sha256(
        _JSON_OBJECT.validate_python(HostedCampaignHandoff.model_json_schema())
    )
    return ToolCapability(
        capability_id=_HANDOFF_CAPABILITY_ID,
        descriptor_sha256=canonical_json_sha256(
            {
                "schema_version": "trace.feature-launch-capability.v1",
                "capability_id": _HANDOFF_CAPABILITY_ID,
                "owner": "trace-marketing.hosted-control-plane",
                "effect_class": "control_plane_write",
                "request_schema_sha256": request_schema_sha256,
                "worst_case_cost_units": 1,
            }
        ),
        request_schema_sha256=request_schema_sha256,
        effect_class="control_plane_write",
        worst_case_cost_units=1,
    )


def _receipt(
    invocation: BoundToolInvocation | None,
    disposition: EffectDisposition,
    evidence: JsonObject,
) -> ToolReceipt:
    if invocation is None:
        raise FeatureLaunchRunError("feature_launch_reconciliation_invocation_missing")
    payload: JsonObject = {
        "schema_version": "trace.hosted-campaign-tool-receipt.v1",
        "call_sha256": invocation.call.digest,
        "disposition": disposition.value,
        "evidence_sha256": canonical_json_sha256(evidence),
    }
    return ToolReceipt(
        call_id=invocation.call.call_id,
        call_sha256=invocation.call.digest,
        approval_grant_sha256=None,
        disposition=disposition,
        actual_cost_units=1,
        receipt_sha256=canonical_json_sha256(payload),
    )


def _validate_hosted_status(status: JsonObject, handoff: HostedCampaignHandoff) -> None:
    try:
        lineage = FeatureLaunchLineage.model_validate(status.get("agent_run_lineage"))
    except ValueError as error:
        raise FeatureLaunchRunError("hosted_campaign_lineage_invalid") from error
    if (
        status.get("account_id") != handoff.account_id
        or status.get("campaign_id") != handoff.campaign_id
        or status.get("feature_packet_sha256") != canonical_json_sha256(handoff.feature_packet)
        or status.get("mode") != "shadow"
        or lineage != handoff.agent_run_lineage
    ):
        raise FeatureLaunchRunError("hosted_campaign_handoff_mismatch")


def _launch_result(
    request: FeatureLaunchRunRequest,
    research: DynamicEvidenceResearchResult,
    lineage: FeatureLaunchLineage,
    state: Literal["created", "blocked", "awaiting_reconciliation"],
    status: JsonObject | None,
) -> FeatureLaunchRunResult:
    return FeatureLaunchRunResult(
        schema_version="trace.feature-launch-run-result.v1",
        agent_run_id=request.agent_run_id,
        state=state,
        research=research,
        lineage=lineage,
        campaign_status=status,
    )

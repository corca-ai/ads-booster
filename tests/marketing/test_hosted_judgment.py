from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, cast

import pytest

from ads_booster.contracts.marketing_agent import (
    ClaimStatus,
    EvidenceKind,
    EvidenceReference,
    EvidenceResult,
    FeatureClaim,
    FeatureEvidencePacket,
    FeatureGate,
    FeatureLifecycle,
    contract_sha256,
)
from ads_booster.contracts.marketing_context import (
    CustomerSignal,
    CustomerSignalConsentStatus,
    CustomerSignalKind,
    CustomerSignalPlanningProjection,
    CustomerSignalSourceKind,
    MarketingContextPlanningProjection,
    MarketingContextSnapshot,
)
from ads_booster.marketing.hosted_judgment import PIPELINE, HostedMarketingJudgmentExecutor
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskStatus

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _json_sha256(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _packet() -> FeatureEvidencePacket:
    return FeatureEvidencePacket(
        schema_version="trace.feature-evidence.v1",
        packet_id="packet-lockscreen-v1",
        feature_id="trace.lockscreen.ai-concepts",
        title="AI lock screen concepts",
        lifecycle=FeatureLifecycle.SOURCE_CANDIDATE,
        repository="corca-ai/Trace_iOS",
        mutable_ref="refs/heads/develop",
        resolved_commit_sha="b" * 40,
        tree_sha="c" * 40,
        claims=(
            FeatureClaim(
                claim_id="claim-scenes",
                text="A character image can be expanded into scheduled scenes.",
                status=ClaimStatus.SOURCE_SUPPORTED,
                evidence_ids=("evidence-source",),
            ),
            FeatureClaim(
                claim_id="claim-released",
                text="The feature is available to every user.",
                status=ClaimStatus.UNSUPPORTED,
            ),
        ),
        evidence=(
            EvidenceReference(
                evidence_id="evidence-source",
                kind=EvidenceKind.SOURCE_BLOB,
                source_uri="https://github.com/corca-ai/Trace_iOS/blob/abc/flow.swift",
                immutable_ref="abc:flow.swift",
                content_sha256="a" * 64,
                result=EvidenceResult.OBSERVED,
                collected_at=NOW,
            ),
        ),
        limitations=("No fresh-installed artifact is bound to this source commit.",),
        gate=FeatureGate(
            publication_allowed=False,
            blocked_claim_ids=("claim-scenes", "claim-released"),
            reasons=("fresh install evidence is missing",),
        ),
        observed_at=NOW,
    )


def _payload() -> JsonObject:
    packet = _packet()
    principles = ["Start from one concrete user situation.", "Show proof before explanation."]
    capabilities = ["strategy.shadow"]
    return cast(
        "JsonObject",
        {
            "pipeline": PIPELINE,
            "judgment": "shadow_strategy",
            "campaign_id": "campaign-1",
            "feature_packet": packet.model_dump(mode="json"),
            "feature_packet_sha256": contract_sha256(packet),
            "account": {
                "account_id": "trace_kr",
                "country": "KR",
                "language": "ko",
                "timezone": "Asia/Seoul",
            },
            "business_outcome": "Increase completed AI lock-screen setups.",
            "current_control": "아이폰 쓰는 유저들, 잠금화면에 일정 넣어봤어?",
            "canonical_principles": principles,
            "knowledge_snapshot_sha256": _json_sha256({"principles": principles}),
            "available_capabilities": capabilities,
            "capability_snapshot_sha256": _json_sha256({"capabilities": capabilities}),
            "requested_by": "hosted_workspace",
        },
    )


def _marketing_context() -> MarketingContextPlanningProjection:
    signal = CustomerSignal(
        schema_version="trace.customer-signal.v1",
        signal_id="signal-character-routine",
        account_id="trace_kr",
        source_kind=CustomerSignalSourceKind.MANUAL_NORMALIZED,
        source_ref="reviewed-interview-batch",
        source_sha256="d" * 64,
        audience_segment_id="ios-character-fans",
        kind=CustomerSignalKind.DESIRED_OUTCOME,
        summary="A familiar character can make daily planning feel personal.",
        caveats=("Small qualitative sample.",),
        confidence_basis_points=6_000,
        consent_status=CustomerSignalConsentStatus.CONFIRMED,
        observed_at=NOW,
        fresh_until=NOW + timedelta(days=14),
        retention_until=NOW + timedelta(days=28),
    )
    snapshot = MarketingContextSnapshot(
        schema_version="trace.marketing-context.v1",
        snapshot_id="context-trace-kr-1",
        account_id="trace_kr",
        brand_guardrails=("Lead with verified product proof.",),
        audience_context=("iPhone users who personalize their lock screen",),
        channel_policy_ids=("threads-organic",),
        customer_signals=(
            CustomerSignalPlanningProjection.from_signal(
                signal,
                signal_sha256=contract_sha256(signal),
            ),
        ),
        approved_by="reviewer-1",
        approved_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    return MarketingContextPlanningProjection.from_snapshot(
        snapshot,
        snapshot_sha256=contract_sha256(snapshot),
    )


def _task(payload: JsonObject | None = None) -> MarketingTask:
    return MarketingTask(
        task_id="task-1",
        run_id="campaign-1",
        account_id="trace_kr",
        kind=TaskKind.MARKETING_JUDGMENT,
        idempotency_key="marketing-judgment:campaign-1",
        payload=_payload() if payload is None else payload,
        created_at=NOW,
    )


def _proposal(
    *, claim_id: str = "claim-scenes", reference_ids: list[str] | None = None
) -> JsonObject:
    return cast(
        "JsonObject",
        {
            "schema_version": "trace.strategy-proposal.v1",
            "business_outcome": "Increase completed AI lock-screen setups.",
            "audience_situation": "An iPhone user wants a favorite character to accompany the day.",
            "belief_to_change": (
                "A lock screen can be a changing story instead of one static image."
            ),
            "hypotheses": [
                {
                    "hypothesis_id": "control",
                    "role": "control",
                    "claim_ids": [claim_id],
                    "value_frame": "A familiar iPhone lock-screen utility hook.",
                    "rationale": "Preserve the established comparison baseline.",
                    "falsifier": "It does not produce attributed setup completions.",
                    "proof_requirement": "Show verified scheduled scenes.",
                    "conversation_motive": "Ask which time slot viewers would use.",
                    "reference_ids": reference_ids or [],
                },
                {
                    "hypothesis_id": "character-day",
                    "role": "challenger",
                    "claim_ids": ["claim-scenes"],
                    "value_frame": "One character lives through several moments of the day.",
                    "rationale": "Continuity may make the feature feel more personal.",
                    "falsifier": "It does not improve the registered outcome over control.",
                    "proof_requirement": "Show the same character in multiple verified scenes.",
                    "conversation_motive": "Ask what the character should do at night.",
                    "reference_ids": [],
                },
            ],
            "experiment": {
                "experiment_id": "experiment-1",
                "manipulated_component": "value frame",
                "held_constant_components": ["account", "posting slot", "call to action"],
                "allowed_incidental_differences": ["necessary connective wording"],
                "activated_hypothesis_ids": ["control", "character-day"],
                "primary_outcome": {
                    "name": "setup_completed",
                    "scope": "direct_response_attribution",
                    "window_hours": 72,
                    "causal_estimand": None,
                },
                "diagnostic_metrics": ["views", "replies"],
                "guardrails": ["unsupported claim", "broken deep link"],
                "minimum_eligible_blocks": 2,
                "maximum_posts": 8,
                "maximum_duration_hours": 336,
                "minimum_attribution_coverage_basis_points": 8_000,
                "stop_rules": ["stop on a product-fidelity violation"],
                "inconclusive_when": ["minimum eligible blocks are not reached"],
            },
        },
    )


@dataclass(slots=True)
class StubCodex:
    result: JsonObject
    prompts: list[str] = field(default_factory=list)

    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        assert schema["type"] == "object"
        assert workspace.is_dir()
        assert timeout_seconds == 240
        self.prompts.append(prompt)
        return self.result


def test_shadow_judgment_binds_strategy_to_evidence_and_receipts(tmp_path: Path) -> None:
    codex = StubCodex(_proposal())
    executor = HostedMarketingJudgmentExecutor(codex=codex, output_root=tmp_path)

    prepared = executor.prepare(_task())
    result = executor.execute(prepared)

    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["publication_allowed"] is False
    assert result.output["context_receipt_sha256"] == prepared.context_receipt_sha256
    strategy = result.output["strategy_brief"]
    assert isinstance(strategy, dict)
    assert strategy["feature_packet_id"] == "packet-lockscreen-v1"
    assert strategy["context_receipt_sha256"] == prepared.context_receipt_sha256
    assert "외부 레퍼런스는 제공되지 않았다" in codex.prompts[0]
    assert "The feature is available to every user" in codex.prompts[0]
    assert len(codex.prompts) == 1


def test_shadow_judgment_binds_only_approved_context_projection_to_its_receipt(
    tmp_path: Path,
) -> None:
    payload = _payload()
    context = _marketing_context()
    payload["marketing_context"] = context.model_dump(mode="json")
    executor = HostedMarketingJudgmentExecutor(codex=StubCodex(_proposal()), output_root=tmp_path)

    prepared = executor.prepare(_task(payload))

    assert prepared.context_receipt.marketing_context == context
    assert "approved customer context" in prepared.prompt
    assert "reviewed-interview-batch" not in prepared.prompt
    assert "consent_status" not in prepared.prompt
    assert "signal-character-routine" in prepared.context_receipt.included_record_ids


def test_shadow_judgment_does_not_send_expired_customer_context_to_codex(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    context = _marketing_context()
    fresh_signal = context.customer_signals[0].model_copy(
        update={"fresh_until": now + timedelta(days=1)}
    )
    expired_context = context.model_copy(
        update={"customer_signals": (fresh_signal,), "expires_at": now - timedelta(seconds=1)}
    )
    payload = _payload()
    payload["marketing_context"] = expired_context.model_dump(mode="json")
    codex = StubCodex(_proposal())
    executor = HostedMarketingJudgmentExecutor(codex=codex, output_root=tmp_path)

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.prepare(_task(payload))

    assert captured.value.failure_code == "marketing_context_expired"
    assert codex.prompts == []


def test_shadow_judgment_rejects_an_unsupported_claim_after_the_codex_turn(
    tmp_path: Path,
) -> None:
    executor = HostedMarketingJudgmentExecutor(
        codex=StubCodex(_proposal(claim_id="claim-released")),
        output_root=tmp_path,
    )
    prepared = executor.prepare(_task())

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.execute(prepared)

    assert captured.value.failure_code == "marketing_judgment_claim_unsupported"
    assert captured.value.unknown_side_effect is True


def test_shadow_judgment_enforces_reference_quarantine(tmp_path: Path) -> None:
    executor = HostedMarketingJudgmentExecutor(
        codex=StubCodex(_proposal(reference_ids=["invented-reference"])),
        output_root=tmp_path,
    )
    prepared = executor.prepare(_task())

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.execute(prepared)

    assert captured.value.failure_code == "marketing_judgment_reference_quarantine_breached"


def test_shadow_judgment_rejects_a_changed_feature_packet_before_admission(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["feature_packet_sha256"] = "f" * 64
    executor = HostedMarketingJudgmentExecutor(codex=StubCodex(_proposal()), output_root=tmp_path)

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.prepare(_task(payload))

    assert captured.value.failure_code == "marketing_judgment_payload_invalid"

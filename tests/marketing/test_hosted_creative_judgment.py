from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.marketing_agent import (
    ClaimStatus,
    EvidenceKind,
    EvidenceReference,
    EvidenceResult,
    ExperimentRegistration,
    FeatureClaim,
    FeatureEvidencePacket,
    FeatureGate,
    FeatureLifecycle,
    MarketingHypothesis,
    OutcomeDefinition,
    OutcomeScope,
    PortfolioRole,
    StrategyBrief,
    contract_sha256,
)
from ads_booster.marketing.hosted_creative_judgment import HostedCreativeJudgmentExecutor
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskStatus

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _packet() -> FeatureEvidencePacket:
    return FeatureEvidencePacket(
        schema_version="trace.feature-evidence.v1",
        packet_id="packet-1",
        feature_id="ai-lock-screen",
        title="AI lock screen concept",
        lifecycle=FeatureLifecycle.SOURCE_CANDIDATE,
        repository="corca-ai/trace",
        mutable_ref="develop",
        resolved_commit_sha="a" * 40,
        tree_sha="b" * 40,
        claims=(
            FeatureClaim(
                claim_id="claim-concept",
                text="A selected character and concept are inputs to scheduled lock screens.",
                status=ClaimStatus.SOURCE_SUPPORTED,
                evidence_ids=("diff-1",),
            ),
        ),
        evidence=(
            EvidenceReference(
                evidence_id="diff-1",
                kind=EvidenceKind.SOURCE_DIFF,
                source_uri="repo://corca-ai/trace",
                immutable_ref="a" * 40,
                content_sha256="c" * 64,
                result=EvidenceResult.OBSERVED,
                collected_at=NOW,
            ),
        ),
        limitations=("installed runtime is not yet verified",),
        gate=FeatureGate(
            publication_allowed=False,
            blocked_claim_ids=("claim-concept",),
            reasons=("source-only evidence",),
        ),
        observed_at=NOW,
    )


def _hypothesis(hypothesis_id: str, role: PortfolioRole) -> MarketingHypothesis:
    return MarketingHypothesis(
        hypothesis_id=hypothesis_id,
        role=role,
        claim_ids=("claim-concept",),
        value_frame=hypothesis_id,
        rationale="Test one belief change.",
        falsifier="Qualified conversation does not improve.",
        proof_requirement="Show the character-time concept without claiming runtime availability.",
        conversation_motive="Ask which character the viewer would choose.",
    )


def _brief(packet: FeatureEvidencePacket) -> StrategyBrief:
    return StrategyBrief(
        schema_version="trace.strategy-brief.v1",
        brief_id="brief-1",
        campaign_id="campaign-1",
        account_id="trace_kr",
        feature_packet_id=packet.packet_id,
        feature_packet_sha256=contract_sha256(packet),
        context_receipt_sha256="d" * 64,
        business_outcome="Find a useful Threads format.",
        audience_situation="An iPhone user who likes character lock screens.",
        belief_to_change="A lock screen can respond to a character's day.",
        hypotheses=(
            _hypothesis("control", PortfolioRole.CONTROL),
            _hypothesis("challenger", PortfolioRole.CHALLENGER),
        ),
        experiment=ExperimentRegistration(
            experiment_id="experiment-1",
            manipulated_component="value frame",
            held_constant_components=("account", "posting slot"),
            activated_hypothesis_ids=("control", "challenger"),
            primary_outcome=OutcomeDefinition(
                name="setup_completed",
                scope=OutcomeScope.DIRECT_RESPONSE_ATTRIBUTION,
                window_hours=48,
            ),
            guardrails=("unsupported claim",),
            minimum_eligible_blocks=4,
            maximum_posts=8,
            maximum_duration_hours=336,
            minimum_attribution_coverage_basis_points=8_000,
            stop_rules=("product fidelity failure",),
            inconclusive_when=("insufficient blocks",),
        ),
        created_at=NOW,
    )


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _task() -> MarketingTask:
    packet = _packet()
    brief = _brief(packet)
    principles = ["proof before media"]
    capability_bindings = []
    for capability_id, owner_id in (
        ("capture.native_png", "trace.native_capture"),
        ("copy.text", "trace.marketing_copy"),
    ):
        binding = {
            "capability_id": capability_id,
            "descriptor_sha256": ("a" if capability_id == "capture.native_png" else "b") * 64,
            "effect_class": "local_artifact",
            "request_schema_sha256": "c" * 64,
            "receipt_schema_sha256": "d" * 64,
            "owner_id": owner_id,
        }
        capability_bindings.append({**binding, "binding_sha256": _digest(binding)})
    capabilities = [binding["capability_id"] for binding in capability_bindings]
    return MarketingTask(
        schema_version="1",
        task_id="creative-task-1",
        run_id="campaign-1.creative.2",
        account_id="trace_kr",
        kind=TaskKind.MARKETING_JUDGMENT,
        idempotency_key="creative:campaign-1:2",
        payload={
            "pipeline": "hosted_marketing_judgment_v1",
            "judgment": "creative_plan",
            "campaign_id": "campaign-1",
            "feature_packet": packet.model_dump(mode="json"),
            "feature_packet_sha256": contract_sha256(packet),
            "strategy_brief": brief.model_dump(mode="json"),
            "strategy_brief_sha256": contract_sha256(brief),
            "account": {
                "account_id": "trace_kr",
                "country": "KR",
                "language": "ko",
                "timezone": "Asia/Seoul",
            },
            "canonical_principles": principles,
            "knowledge_snapshot_sha256": _digest({"principles": principles}),
            "available_capabilities": capabilities,
            "capability_bindings": capability_bindings,
            "capability_snapshot_sha256": _digest({"capability_bindings": capability_bindings}),
            "requested_by": "hosted_workspace",
        },
        created_at=NOW,
    )


def _proposal(
    *,
    capability: str = "copy.text",
    claim_id: str = "claim-concept",
) -> JsonObject:
    def treatment(hypothesis_id: str) -> JsonObject:
        return {
            "treatment_id": f"treatment-{hypothesis_id}",
            "hypothesis_id": hypothesis_id,
            "format": "explanatory_carousel",
            "hook": f"hook {hypothesis_id}",
            "caption_direction": "Explain one product belief.",
            "manipulated_component_value": hypothesis_id,
            "proof_narrative": "Label the sequence as a concept backed by source evidence.",
            "claim_ids": [claim_id],
            "artifact_requests": [
                {
                    "request_id": f"request-{hypothesis_id}",
                    "capability_id": capability,
                    "proof_kind": "copy_only",
                    "claim_ids": [claim_id],
                    "instructions": "Compose a source-labeled explanation.",
                }
            ],
        }

    return {
        "schema_version": "trace.creative-plan-proposal.v1",
        "treatments": [treatment("control"), treatment("challenger")],
    }


@dataclass
class FakeCodex:
    response: JsonObject
    calls: list[tuple[str, JsonObject, Path, float]] = field(default_factory=list)

    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        self.calls.append((prompt, schema, workspace, timeout_seconds))
        return self.response


def test_creative_judgment_selects_proof_without_creating_tool_actions(tmp_path: Path) -> None:
    codex = FakeCodex(_proposal())
    executor = HostedCreativeJudgmentExecutor(codex=codex, output_root=tmp_path)

    result = executor.execute(executor.prepare(_task()))

    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["judgment"] == "creative_plan"
    assert result.output["publication_allowed"] is False
    assert result.output["tool_actions_created"] == 0
    assert result.output["media_plan"]["human_review_required"] is True
    assert len(result.output["media_plan"]["treatments"]) == 2
    assert len(codex.calls) == 1
    assert "도구 호출" in codex.calls[0][0]


@pytest.mark.parametrize(
    ("proposal", "failure"),
    [
        (_proposal(capability="design.figma"), "creative_judgment_result_invalid"),
        (_proposal(claim_id="claim-invented"), "creative_judgment_result_invalid"),
    ],
)
def test_creative_judgment_rejects_capability_and_claim_escape(
    tmp_path: Path,
    proposal: JsonObject,
    failure: str,
) -> None:
    executor = HostedCreativeJudgmentExecutor(
        codex=FakeCodex(proposal),
        output_root=tmp_path,
    )

    with pytest.raises(MarketingExecutionError, match=failure) as caught:
        _ = executor.execute(executor.prepare(_task()))

    assert caught.value.unknown_side_effect is True

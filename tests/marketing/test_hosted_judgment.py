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
    ExperimentEvaluation,
    FeatureClaim,
    FeatureEvidencePacket,
    FeatureGate,
    FeatureLifecycle,
    MarketingReassessment,
    NextExperimentDraft,
    StrategyBrief,
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
from ads_booster.marketing.hosted_reference_research import (
    MarketObservation,
    ReferenceResearchSnapshot,
    ReferenceSource,
    ReferenceSourceReceipt,
    ReferenceVerificationBundle,
)
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
            "agent_run_lineage": {
                "schema_version": "trace.feature-launch-lineage.v1",
                "agent_run_id": "campaign-1",
                "research_session_id": "local-research-1",
                "research_input_sha256": "1" * 64,
                "research_trace_sha256": "2" * 64,
                "research_continuation_sha256": "3" * 64,
            },
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


def _reference_context() -> tuple[ReferenceResearchSnapshot, ReferenceVerificationBundle]:
    sources = (
        ReferenceSource(
            source_id="source-one",
            url="https://example.com/one",
            title="One",
            source_type="article",
            summary="Generic hooks are common.",
            accessed_at="2026-08-31T00:00:00Z",
        ),
        ReferenceSource(
            source_id="source-two",
            url="https://example.org/two",
            title="Two",
            source_type="threads_post",
            summary="Day sequences invite replies.",
            accessed_at="2026-08-31T00:00:00Z",
        ),
    )
    snapshot = ReferenceResearchSnapshot(
        schema_version="trace.reference-research.v1",
        snapshot_id="snapshot-bound-1",
        campaign_id="campaign-1",
        feature_packet_sha256=contract_sha256(_packet()),
        sources=sources,
        observations=(
            MarketObservation(
                observation_id="observation-one",
                classification="saturation",
                statement="The current control is saturated.",
                source_ids=("source-one",),
                confidence_basis="Observed repetition.",
            ),
            MarketObservation(
                observation_id="observation-two",
                classification="format_mechanic",
                statement="A day sequence can invite replies.",
                source_ids=("source-two",),
                confidence_basis="Observed audience language.",
            ),
        ),
        blind_spots=("No private conversion data.",),
        quarantine=True,
        collected_at="2026-08-31T00:00:00Z",
    )
    snapshot_sha256 = contract_sha256(snapshot)
    verification = ReferenceVerificationBundle(
        schema_version="trace.reference-verification.v1",
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=snapshot_sha256,
        receipts=tuple(
            ReferenceSourceReceipt(
                schema_version="trace.reference-source-receipt.v1",
                receipt_id=f"source-receipt-{index}",
                source_id=source.source_id,
                requested_url=source.url,
                final_url=source.url,
                http_status=200,
                content_type="text/html",
                content_sha256=str(index) * 64,
                byte_length=100 + index,
                fetched_at="2026-08-31T00:00:00Z",
            )
            for index, source in enumerate(sources, start=1)
        ),
        verified_at="2026-08-31T00:00:00Z",
    )
    return snapshot, verification


def _task(payload: JsonObject | None = None) -> MarketingTask:
    task_payload = _payload() if payload is None else payload
    return MarketingTask(
        task_id="task-1",
        run_id=cast("str", task_payload["campaign_id"]),
        account_id="trace_kr",
        kind=TaskKind.MARKETING_JUDGMENT,
        idempotency_key="marketing-judgment:campaign-1",
        payload=task_payload,
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
            "decision_dossier": {
                "schema_version": "trace.marketing-decision-dossier.v1",
                "situation": "new_launch",
                "selected_icp_id": "research_needed",
                "selection_basis_ids": ["evidence-source"],
                "positioning": {
                    "category": "dynamic lock-screen companion",
                    "current_alternative": "one static lock-screen image",
                    "differentiated_mechanism": (
                        "scheduled scenes keep one character present through the day"
                    ),
                    "proof_claim_ids": ["claim-scenes"],
                },
                "evidence_dispositions": [
                    {
                        "evidence_id": "evidence-source",
                        "disposition": "supports",
                        "confidence_basis_points": 7000,
                        "freshness": "unknown",
                        "use": "test",
                        "reason": "Source evidence supports the mechanism but not a validated ICP.",
                    }
                ],
                "recommended_next_step": "research",
                "reason": "Identify a validated audience segment before assisted execution.",
                "required_proof_ids": ["evidence-source"],
            },
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


def _successor_case(*, control_reference_id: str | None = None) -> tuple[JsonObject, JsonObject]:
    packet = _packet()
    ordinary = _proposal()
    strategy = StrategyBrief.model_validate(
        {
            "schema_version": "trace.strategy-brief.v1",
            "brief_id": "brief-source",
            "campaign_id": "campaign-source",
            "account_id": "trace_kr",
            "feature_packet_id": packet.packet_id,
            "feature_packet_sha256": contract_sha256(packet),
            "context_receipt_sha256": "4" * 64,
            "business_outcome": ordinary["business_outcome"],
            "audience_situation": ordinary["audience_situation"],
            "belief_to_change": ordinary["belief_to_change"],
            "decision_dossier": ordinary["decision_dossier"],
            "hypotheses": ordinary["hypotheses"],
            "experiment": ordinary["experiment"],
            "created_at": NOW,
        }
    )
    if control_reference_id is not None:
        strategy = strategy.model_copy(
            update={
                "hypotheses": (
                    strategy.hypotheses[0].model_copy(
                        update={"reference_ids": (control_reference_id,)}
                    ),
                    strategy.hypotheses[1],
                )
            }
        )
    evaluation = ExperimentEvaluation.model_validate(
        {
            "schema_version": "trace.experiment-evaluation.v1",
            "evaluation_id": "evaluation-source",
            "campaign_id": "campaign-source",
            "experiment_id": strategy.experiment.experiment_id,
            "state": "evaluated",
            "outcome_scope": "direct_response_attribution",
            "eligible_blocks": 2,
            "attribution_coverage_basis_points": 8000,
            "winner_hypothesis_id": "character-day",
            "causal_estimate": None,
            "interpretation": "The challenger led on attributed setup completion.",
            "guardrail_failures": [],
            "lineage_ids": ["assignment-1", "assignment-2"],
            "evaluated_at": NOW,
        }
    )
    dossier = dict(cast("dict[str, object]", ordinary["decision_dossier"]))
    dossier.update(
        {
            "situation": "experiment_result",
            "recommended_next_step": "design_experiment",
            "selected_icp_id": "ios-character-fans",
        }
    )
    reassessment = MarketingReassessment.model_validate(
        {
            "schema_version": "trace.marketing-reassessment.v1",
            "reassessment_id": "reassessment-source",
            "campaign_id": "campaign-source",
            "trigger_evaluation_id": evaluation.evaluation_id,
            "trigger_evaluation_sha256": contract_sha256(evaluation),
            "situation": "experiment_result",
            "decision_dossier": dossier,
            "hypothesis_reassessments": [
                {
                    "hypothesis_id": "control",
                    "disposition": "retain",
                    "rationale": "Keep the baseline.",
                    "next_test": None,
                },
                {
                    "hypothesis_id": "character-day",
                    "disposition": "revise",
                    "rationale": "Isolate the opening value frame.",
                    "next_test": "Change only the opening value frame.",
                },
            ],
            "created_at": NOW,
        }
    )
    draft = NextExperimentDraft.model_validate(
        {
            "schema_version": "trace.next-experiment-draft.v1",
            "draft_id": "next-experiment-draft-source",
            "campaign_id": "campaign-source",
            "account_id": "trace_kr",
            "trigger_evaluation_id": evaluation.evaluation_id,
            "trigger_evaluation_sha256": contract_sha256(evaluation),
            "trigger_reassessment_id": reassessment.reassessment_id,
            "trigger_reassessment_sha256": contract_sha256(reassessment),
            "prior_strategy_sha256": contract_sha256(strategy),
            "control_hypothesis_id": "control",
            "primary_outcome": strategy.experiment.primary_outcome,
            "held_constant_components": strategy.experiment.held_constant_components,
            "source_hypothesis_ids": ["character-day"],
            "supporting_claim_ids": ["claim-scenes"],
            "evidence": [
                {"evidence_id": "evaluation-source", "interpretation": "A direction emerged."}
            ],
            "counterevidence": [],
            "assumptions": ["The same audience remains reachable."],
            "candidate": {
                "parent_hypothesis_ids": ["character-day"],
                "claim_ids": ["claim-scenes"],
                "audience_situation": "A character fan wants continuity through the day.",
                "belief_to_change": "The changing scenes create continuity, not novelty alone.",
                "hypothesis": "A continuity-first opening improves attributed setups.",
                "rationale": "It isolates the mechanism suggested by the observed direction.",
                "manipulated_component": "opening value frame",
                "treatment_concept": "Open on one character moving through the day.",
                "expected_signal": "Higher attributed setup completion than control.",
                "falsifier": "The direction does not repeat across eligible blocks.",
            },
            "effect_class": "none",
            "state": "draft",
            "human_review_required": True,
            "created_at": NOW,
        }
    )
    successor_control = strategy.hypotheses[0].model_copy(
        update={"hypothesis_id": "successor-control"}
    )
    proposal = cast(
        "JsonObject",
        {
            "schema_version": "trace.strategy-proposal.v1",
            "business_outcome": strategy.business_outcome,
            "audience_situation": draft.candidate.audience_situation,
            "belief_to_change": draft.candidate.belief_to_change,
            "decision_dossier": reassessment.decision_dossier.model_dump(mode="json"),
            "hypotheses": [
                successor_control.model_dump(mode="json"),
                {
                    "hypothesis_id": "successor-challenger",
                    "role": "challenger",
                    "claim_ids": ["claim-scenes"],
                    "value_frame": draft.candidate.treatment_concept,
                    "rationale": (f"{draft.candidate.hypothesis}\n\n{draft.candidate.rationale}"),
                    "falsifier": draft.candidate.falsifier,
                    "proof_requirement": f"Expected signal: {draft.candidate.expected_signal}",
                    "conversation_motive": (
                        "Discuss the approved experiment without changing its hypothesis."
                    ),
                    "reference_ids": [],
                },
            ],
            "experiment": {
                **strategy.experiment.model_dump(mode="json"),
                "experiment_id": "successor-experiment",
                "manipulated_component": draft.candidate.manipulated_component,
                "activated_hypothesis_ids": ["successor-control", "successor-challenger"],
            },
        },
    )
    payload = _payload()
    successor_packet = packet.model_copy(update={"packet_id": "packet-lockscreen-successor"})
    payload.update(
        {
            "campaign_id": "campaign-successor",
            "feature_packet": successor_packet.model_dump(mode="json"),
            "feature_packet_sha256": contract_sha256(successor_packet),
            "agent_run_lineage": None,
            "next_experiment_seed": {
                "schema_version": "trace.successor-strategy-seed.v1",
                "activation_id": "activation-1",
                "successor_campaign_id": "campaign-successor",
                "successor_control_hypothesis_id": "successor-control",
                "successor_challenger_hypothesis_id": "successor-challenger",
                "successor_experiment_id": "successor-experiment",
                "source_campaign_id": "campaign-source",
                "source_feature_packet_sha256": contract_sha256(packet),
                "successor_feature_packet_sha256": contract_sha256(successor_packet),
                "source_lineage_sha256": "5" * 64,
                "request_sha256": "6" * 64,
                "approval_grant_id": "grant-1",
                "approved_by": "reviewer-1",
                "approved_at": NOW.isoformat().replace("+00:00", "Z"),
                "prior_strategy": strategy.model_dump(mode="json"),
                "prior_strategy_sha256": contract_sha256(strategy),
                "evaluation": evaluation.model_dump(mode="json"),
                "evaluation_sha256": contract_sha256(evaluation),
                "reassessment": reassessment.model_dump(mode="json"),
                "reassessment_sha256": contract_sha256(reassessment),
                "approved_draft": draft.model_dump(mode="json"),
                "approved_draft_sha256": contract_sha256(draft),
            },
        }
    )
    return payload, proposal


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


def test_successor_strategy_preserves_the_approved_experiment_constraints(
    tmp_path: Path,
) -> None:
    payload, proposal = _successor_case()
    task = _task(payload)
    codex = StubCodex(proposal)
    executor = HostedMarketingJudgmentExecutor(codex=codex, output_root=tmp_path)

    result = executor.execute(executor.prepare(task))

    assert result.status is TaskStatus.SUCCEEDED
    brief = result.output["strategy_brief"]
    assert isinstance(brief, dict)
    assert brief["campaign_id"] == "campaign-successor"
    experiment = cast("dict[str, object]", brief["experiment"])
    assert experiment["experiment_id"] == "successor-experiment"
    assert "approved successor constraints" in codex.prompts[0]
    receipt = result.output["context_receipt"]
    assert isinstance(receipt, dict)
    included_record_ids = cast("list[object]", receipt["included_record_ids"])
    assert "activation-1" in included_record_ids


def test_successor_strategy_cannot_replace_the_approved_candidate(tmp_path: Path) -> None:
    payload, proposal = _successor_case()
    hypotheses = cast("list[dict[str, object]]", proposal["hypotheses"])
    hypotheses[1]["value_frame"] = "A different treatment invented after approval."
    executor = HostedMarketingJudgmentExecutor(
        codex=StubCodex(proposal),
        output_root=tmp_path,
    )

    with pytest.raises(
        MarketingExecutionError,
        match="marketing_successor_constraints_changed",
    ):
        _ = executor.execute(executor.prepare(_task(payload)))


def test_successor_preserves_source_control_references_without_reusing_them_for_challenger(
    tmp_path: Path,
) -> None:
    payload, proposal = _successor_case(control_reference_id="source-reference-1")
    executor = HostedMarketingJudgmentExecutor(
        codex=StubCodex(proposal),
        output_root=tmp_path,
    )

    result = executor.execute(executor.prepare(_task(payload)))
    assert result.status is TaskStatus.SUCCEEDED

    hypotheses = cast("list[dict[str, object]]", proposal["hypotheses"])
    hypotheses[1]["reference_ids"] = ["source-reference-1"]
    changed_executor = HostedMarketingJudgmentExecutor(
        codex=StubCodex(proposal),
        output_root=tmp_path / "challenger",
    )
    with pytest.raises(
        MarketingExecutionError,
        match="marketing_judgment_reference_quarantine_breached",
    ):
        _ = changed_executor.execute(changed_executor.prepare(_task(payload)))


def test_shadow_judgment_binds_strategy_to_evidence_and_receipts(tmp_path: Path) -> None:
    codex = StubCodex(_proposal())
    executor = HostedMarketingJudgmentExecutor(codex=codex, output_root=tmp_path)

    prepared = executor.prepare(_task())
    result = executor.execute(prepared)

    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["publication_allowed"] is False
    assert result.output["context_receipt_sha256"] == prepared.context_receipt_sha256
    assert result.output["agent_run_lineage"] == _payload()["agent_run_lineage"]
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


def test_shadow_judgment_binds_server_fetched_reference_receipts(tmp_path: Path) -> None:
    payload = _payload()
    snapshot, verification = _reference_context()
    payload["reference_snapshot"] = snapshot.model_dump(mode="json")
    payload["reference_snapshot_sha256"] = contract_sha256(snapshot)
    payload["reference_verification"] = verification.model_dump(mode="json")
    payload["reference_verification_sha256"] = contract_sha256(verification)
    executor = HostedMarketingJudgmentExecutor(codex=StubCodex(_proposal()), output_root=tmp_path)

    prepared = executor.prepare(_task(payload))

    assert "source-receipt-1" in prepared.context_receipt.included_record_ids
    assert "source-receipt-2" in prepared.context_receipt.included_record_ids
    assert "server-fetched source receipts" in prepared.prompt
    assert verification.receipts[0].content_sha256 in prepared.prompt


def test_shadow_judgment_rejects_a_rewritten_reference_receipt(tmp_path: Path) -> None:
    payload = _payload()
    snapshot, verification = _reference_context()
    payload["reference_snapshot"] = snapshot.model_dump(mode="json")
    payload["reference_snapshot_sha256"] = contract_sha256(snapshot)
    payload["reference_verification"] = verification.model_dump(mode="json")
    payload["reference_verification_sha256"] = contract_sha256(verification)
    rewritten = cast("dict[str, object]", payload["reference_verification"])
    receipts = cast("list[dict[str, object]]", rewritten["receipts"])
    receipts[0]["content_sha256"] = "9" * 64
    executor = HostedMarketingJudgmentExecutor(codex=StubCodex(_proposal()), output_root=tmp_path)

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.prepare(_task(payload))

    assert captured.value.failure_code == "marketing_judgment_payload_invalid"


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


def test_shadow_judgment_rejects_an_unbound_required_proof(tmp_path: Path) -> None:
    proposal = _proposal()
    dossier = cast("dict[str, object]", proposal["decision_dossier"])
    dossier["required_proof_ids"] = ["invented-proof"]
    executor = HostedMarketingJudgmentExecutor(codex=StubCodex(proposal), output_root=tmp_path)
    prepared = executor.prepare(_task())

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.execute(prepared)

    assert captured.value.failure_code == "marketing_judgment_required_proof_unbound"


def test_shadow_judgment_rejects_rewritten_inconclusive_feature_evidence(
    tmp_path: Path,
) -> None:
    packet = _packet()
    inconclusive = packet.evidence[0].model_copy(update={"result": EvidenceResult.INCONCLUSIVE})
    packet = packet.model_copy(update={"evidence": (inconclusive,)})
    payload = _payload()
    payload["feature_packet"] = packet.model_dump(mode="json")
    payload["feature_packet_sha256"] = contract_sha256(packet)
    executor = HostedMarketingJudgmentExecutor(
        codex=StubCodex(_proposal()),
        output_root=tmp_path,
    )
    prepared = executor.prepare(_task(payload))

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.execute(prepared)

    assert captured.value.failure_code == "marketing_judgment_evidence_result_rewritten"


def test_shadow_judgment_rejects_a_changed_feature_packet_before_admission(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["feature_packet_sha256"] = "f" * 64
    executor = HostedMarketingJudgmentExecutor(codex=StubCodex(_proposal()), output_root=tmp_path)

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.prepare(_task(payload))

    assert captured.value.failure_code == "marketing_judgment_payload_invalid"

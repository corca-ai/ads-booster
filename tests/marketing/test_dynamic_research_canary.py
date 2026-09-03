from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest
from pydantic import ValidationError

from ads_booster.contracts.marketing_agent import (
    ClaimStatus,
    EvidenceKind,
    EvidenceReference,
    EvidenceResult,
    FeatureClaim,
    FeatureEvidencePacket,
    FeatureGate,
    FeatureLifecycle,
)
from ads_booster.contracts.marketing_context import (
    CustomerSignalKind,
    CustomerSignalPlanningProjection,
    MarketingContextPlanningProjection,
)
from ads_booster.marketing.dynamic_evidence_research import (
    DynamicEvidenceResearchRequest,
    planner_protocol_sha256,
)
from ads_booster.marketing.dynamic_research_canary import (
    DynamicResearchCanaryCase,
    DynamicResearchCanaryExpectation,
    DynamicResearchCanaryInput,
    DynamicResearchCanaryReport,
    DynamicResearchProviderTrialRunner,
    DynamicResearchRuntimeIdentity,
    DynamicResearchSemanticAnchor,
    evaluate_dynamic_research_canary,
)
from ads_booster.marketing.dynamic_research_canary_corpus import (
    load_private_dynamic_research_canary_cases,
)
from ads_booster.marketing.evidence_research_operator import ResearchScope

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 3, tzinfo=UTC)


class AdaptiveCodex:
    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        _ = schema, workspace, timeout_seconds
        if '"prior_observations":[]' in prompt:
            scope = ResearchScope.CUSTOMER_INTELLIGENCE
            focus = "customer demand"
        else:
            scope = ResearchScope.PRODUCT_TRUTH
            focus = "privacy objection" if "privacy" in prompt else "personal continuity"
        return {
            "action_id": f"observe.{scope.value}",
            "scope": scope.value,
            "claim_ids": ["claim-feature"],
            "research_question": f"Which installed proof resolves {focus}?",
            "counter_evidence_question": f"What would overturn {focus}?",
        }

    def run_marketing_research_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        raise AssertionError((prompt, schema, workspace, timeout_seconds))


class FixedRouterCodex(AdaptiveCodex):
    @override
    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        _ = schema, workspace, timeout_seconds
        scope = (
            ResearchScope.CUSTOMER_INTELLIGENCE
            if '"prior_observations":[]' in prompt
            else ResearchScope.PRODUCT_TRUTH
        )
        return {
            "action_id": f"observe.{scope.value}",
            "scope": scope.value,
            "claim_ids": ["claim-feature"],
            "research_question": "Which generic fact should be checked next?",
            "counter_evidence_question": "What generic fact would disprove it?",
        }


def test_comparative_canary_defeats_a_fixed_router_without_claiming_superiority(
    tmp_path: Path,
) -> None:
    cases = _cases()
    candidate = _runner("trace-adaptive", AdaptiveCodex(), tmp_path / "candidate")
    baseline = _runner("generic-fixed", FixedRouterCodex(), tmp_path / "baseline")

    report = evaluate_dynamic_research_canary(cases, candidate, baseline, trials=2)

    assert report.candidate_process_valid
    assert report.candidate_noninferior
    assert all(item.passed for item in report.candidate_pair_results)
    assert not any(item.passed for item in report.baseline_pair_results)
    assert report.blind_candidate_preference_rate is None
    assert report.superiority_claim_allowed is False
    assert all(item.marketing_quality_passed for item in report.candidate_results)
    assert not any(item.marketing_quality_passed for item in report.baseline_results)


def test_canary_requires_the_same_runtime_and_tool_environment(tmp_path: Path) -> None:
    candidate = _runner("trace-adaptive", AdaptiveCodex(), tmp_path / "candidate")
    baseline = _runner("generic-fixed", FixedRouterCodex(), tmp_path / "baseline")
    baseline = replace(
        baseline,
        runtime_identity=baseline.runtime_identity.model_copy(
            update={"tool_environment_sha256": "9" * 64}
        ),
    )

    with pytest.raises(ValueError, match="dynamic_research_runtimes_not_comparable"):
        _ = evaluate_dynamic_research_canary(_cases(), candidate, baseline, trials=2)


def test_pair_rejects_more_than_the_named_customer_fact() -> None:
    left, right = _cases()
    changed_request = right.input.request.model_copy(update={"max_cost_units": 5})
    changed = DynamicResearchCanaryCase(
        right.input.model_copy(update={"request": changed_request}),
        right.expectation,
    )

    with pytest.raises(ValueError, match="dynamic_research_pair_context_mismatch"):
        _ = evaluate_dynamic_research_canary(
            (left, changed),
            _runner("trace-adaptive", AdaptiveCodex(), Path("candidate")),
            _runner("generic-fixed", FixedRouterCodex(), Path("baseline")),
            trials=2,
        )


def test_report_cannot_be_relabelled_superior_without_private_evidence(
    tmp_path: Path,
) -> None:
    report = evaluate_dynamic_research_canary(
        _cases(),
        _runner("trace-adaptive", AdaptiveCodex(), tmp_path / "candidate"),
        _runner("generic-fixed", FixedRouterCodex(), tmp_path / "baseline"),
        trials=2,
    )
    payload = report.model_dump(mode="json")
    payload["superiority_claim_allowed"] = True

    with pytest.raises(ValidationError, match="superiority lacks sufficient evidence"):
        _ = DynamicResearchCanaryReport.model_validate(payload)


def test_private_loader_keeps_inputs_and_expectations_in_separate_files(
    tmp_path: Path,
) -> None:
    cases = _cases()
    _ = (tmp_path / "runner_inputs.json").write_text(
        json.dumps(
            {
                "schema_version": "trace.dynamic-research-canary-inputs.v1",
                "cases": [item.input.model_dump(mode="json") for item in cases],
            }
        ),
        encoding="utf-8",
    )
    _ = (tmp_path / "grader_expectations.json").write_text(
        json.dumps(
            {
                "schema_version": "trace.dynamic-research-canary-expectations.v1",
                "cases": [item.expectation.model_dump(mode="json") for item in cases],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_private_dynamic_research_canary_cases(tmp_path)

    assert tuple(item.input for item in loaded) == tuple(item.input for item in cases)
    assert tuple(item.expectation for item in loaded) == tuple(item.expectation for item in cases)
    assert "semantic_anchors" not in (tmp_path / "runner_inputs.json").read_text()


def _runner(
    runner_id: str,
    codex: AdaptiveCodex,
    output_root: Path,
) -> DynamicResearchProviderTrialRunner:
    return DynamicResearchProviderTrialRunner(
        codex=codex,
        output_root=output_root,
        runtime_identity=DynamicResearchRuntimeIdentity(
            schema_version="trace.dynamic-research-runtime.v1",
            runner_id=runner_id,
            provider_id="fixture-provider",
            requested_model_id="same-model",
            executable_name="fixture-codex",
            executable_sha256="1" * 64,
            executable_version="fixture-codex 1.0",
            package_version="0.0.0",
            planner_protocol_sha256=planner_protocol_sha256(),
            tool_environment_sha256="3" * 64,
        ),
    )


def _cases() -> tuple[DynamicResearchCanaryCase, DynamicResearchCanaryCase]:
    return (
        _case(
            "continuity-signal",
            summary="Users value personal continuity across the day.",
            signal_sha="4" * 64,
            snapshot_sha="5" * 64,
            concept="personal continuity",
        ),
        _case(
            "privacy-signal",
            summary="Users object to privacy ambiguity in generated scenes.",
            signal_sha="6" * 64,
            snapshot_sha="7" * 64,
            concept="privacy objection",
        ),
    )


def _case(
    case_id: str,
    *,
    summary: str,
    signal_sha: str,
    snapshot_sha: str,
    concept: str,
) -> DynamicResearchCanaryCase:
    return DynamicResearchCanaryCase(
        input=DynamicResearchCanaryInput(
            schema_version="trace.dynamic-research-canary-input.v1",
            case_id=case_id,
            request=DynamicEvidenceResearchRequest(
                schema_version="trace.dynamic-evidence-research-request.v1",
                session_id="paired-research",
                account_id="trace-kr",
                feature_packet=_packet(),
                required_scopes=(
                    ResearchScope.CUSTOMER_INTELLIGENCE,
                    ResearchScope.PRODUCT_TRUTH,
                ),
                marketing_context=_context(
                    summary=summary,
                    signal_sha=signal_sha,
                    snapshot_sha=snapshot_sha,
                ),
                market_context=None,
                max_tool_calls=2,
                max_cost_units=4,
            ),
        ),
        expectation=DynamicResearchCanaryExpectation(
            schema_version="trace.dynamic-research-canary-expectation.v1",
            case_id=case_id,
            expected_terminal_state="completed",
            semantic_anchors=(
                DynamicResearchSemanticAnchor(
                    anchor_id=f"{case_id}-research",
                    field="research_questions",
                    any_of=(concept,),
                ),
                DynamicResearchSemanticAnchor(
                    anchor_id=f"{case_id}-counter",
                    field="counter_evidence_questions",
                    any_of=(concept,),
                ),
            ),
            forbidden_phrases=("grader-secret-sentinel",),
            counterfactual_pair_id="customer-fact-flip",
            perturbed_signal_id="signal-one",
            required_pair_differences=(
                "research_questions",
                "counter_evidence_questions",
            ),
        ),
    )


def _packet() -> FeatureEvidencePacket:
    return FeatureEvidencePacket(
        schema_version="trace.feature-evidence.v1",
        packet_id="packet-dynamic-canary",
        feature_id="ai-lock-screen-concept",
        title="AI Lock Screen Concept",
        lifecycle=FeatureLifecycle.INSTALLED_CONFIRMED,
        repository="corca-ai/trace",
        mutable_ref="develop",
        resolved_commit_sha="1" * 40,
        tree_sha="2" * 40,
        claims=(
            FeatureClaim(
                claim_id="claim-feature",
                text="Scheduled scenes change through the day.",
                status=ClaimStatus.INSTALLED_CONFIRMED,
                evidence_ids=("installed-proof",),
            ),
        ),
        evidence=(
            EvidenceReference(
                evidence_id="installed-proof",
                kind=EvidenceKind.INSTALL_RECEIPT,
                source_uri="trace://installed-proof",
                immutable_ref="install-one",
                content_sha256="8" * 64,
                result=EvidenceResult.PASSED,
                collected_at=NOW,
            ),
        ),
        limitations=("Only the installed claim is usable.",),
        gate=FeatureGate(publication_allowed=True, allowed_claim_ids=("claim-feature",)),
        observed_at=NOW,
    )


def _context(
    *,
    summary: str,
    signal_sha: str,
    snapshot_sha: str,
) -> MarketingContextPlanningProjection:
    return MarketingContextPlanningProjection(
        schema_version="trace.marketing-context-projection.v1",
        snapshot_id=f"snapshot-{snapshot_sha[0]}",
        snapshot_sha256=snapshot_sha,
        account_id="trace-kr",
        brand_guardrails=("Do not overclaim automation.",),
        audience_context=("Korean iPhone users",),
        channel_policy_ids=("threads-default-off",),
        customer_signals=(
            CustomerSignalPlanningProjection(
                schema_version="trace.customer-signal-projection.v1",
                signal_id="signal-one",
                signal_sha256=signal_sha,
                audience_segment_id="iphone-users",
                kind=CustomerSignalKind.DESIRED_OUTCOME,
                summary=summary,
                caveats=("Small qualitative sample.",),
                confidence_basis_points=6500,
                observed_at=NOW - timedelta(days=1),
                fresh_until=NOW + timedelta(days=30),
            ),
        ),
        expires_at=NOW + timedelta(days=7),
    )

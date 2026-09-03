from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from ads_booster.contracts.marketing_agent import (
    ExperimentEvaluation,
    StrategyBrief,
    contract_sha256,
)
from ads_booster.marketing.hosted_reassessment_judgment import (
    HostedOutcomeReassessmentExecutor,
    derive_reassessment_situation,
)
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskStatus

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 2, tzinfo=UTC)


def _prior_strategy() -> StrategyBrief:
    return StrategyBrief.model_validate(
        {
            "schema_version": "trace.strategy-brief.v1",
            "brief_id": "brief-1",
            "campaign_id": "campaign-1",
            "account_id": "trace_kr",
            "feature_packet_id": "packet-1",
            "feature_packet_sha256": "a" * 64,
            "context_receipt_sha256": "b" * 64,
            "business_outcome": "Increase completed lock-screen setups.",
            "audience_situation": "An iPhone user wants a character through the day.",
            "belief_to_change": "A lock screen can evolve instead of staying static.",
            "decision_dossier": {
                "schema_version": "trace.marketing-decision-dossier.v1",
                "situation": "new_launch",
                "selected_icp_id": "ios-character-fans",
                "selection_basis_ids": ["signal-1"],
                "positioning": {
                    "category": "dynamic lock-screen companion",
                    "current_alternative": "a static lock-screen image",
                    "differentiated_mechanism": "one character changes with the day",
                    "proof_claim_ids": ["claim-1"],
                },
                "evidence_dispositions": [
                    {
                        "evidence_id": "signal-1",
                        "disposition": "supports",
                        "confidence_basis_points": 7000,
                        "freshness": "fresh",
                        "use": "use_as_constraint",
                        "reason": "An approved customer signal supports the audience.",
                    }
                ],
                "recommended_next_step": "design_experiment",
                "reason": "The audience signal is sufficient for a bounded experiment.",
                "required_proof_ids": ["claim-1"],
            },
            "hypotheses": [
                {
                    "hypothesis_id": "control",
                    "role": "control",
                    "claim_ids": ["claim-1"],
                    "value_frame": "static utility hook",
                    "rationale": "Preserve the known baseline.",
                    "falsifier": "It produces no attributed setup completions.",
                    "proof_requirement": "Show the installed scheduled scenes.",
                    "conversation_motive": "Ask which scene viewers want.",
                    "reference_ids": [],
                },
                {
                    "hypothesis_id": "challenger",
                    "role": "challenger",
                    "claim_ids": ["claim-1"],
                    "value_frame": "character continuity hook",
                    "rationale": "Continuity may make the feature easier to understand.",
                    "falsifier": "It does not beat the registered control.",
                    "proof_requirement": "Show one character across verified scenes.",
                    "conversation_motive": "Ask which character should appear next.",
                    "reference_ids": [],
                },
            ],
            "experiment": {
                "experiment_id": "experiment-1",
                "manipulated_component": "value frame",
                "held_constant_components": ["account", "posting slot"],
                "allowed_incidental_differences": [],
                "activated_hypothesis_ids": ["control", "challenger"],
                "primary_outcome": {
                    "name": "setup_completed",
                    "scope": "direct_response_attribution",
                    "window_hours": 72,
                    "causal_estimand": None,
                },
                "diagnostic_metrics": ["views"],
                "guardrails": ["product fidelity"],
                "minimum_eligible_blocks": 2,
                "maximum_posts": 4,
                "maximum_duration_hours": 336,
                "minimum_attribution_coverage_basis_points": 8000,
                "stop_rules": ["stop on a fidelity violation"],
                "inconclusive_when": ["minimum blocks are not reached"],
            },
            "created_at": NOW,
        }
    )


def _evaluation(**overrides: object) -> ExperimentEvaluation:
    value: dict[str, object] = {
        "schema_version": "trace.experiment-evaluation.v1",
        "evaluation_id": "evaluation-1",
        "campaign_id": "campaign-1",
        "experiment_id": "experiment-1",
        "state": "evaluated",
        "outcome_scope": "direct_response_attribution",
        "eligible_blocks": 2,
        "attribution_coverage_basis_points": 10000,
        "winner_hypothesis_id": "challenger",
        "causal_estimate": None,
        "interpretation": "The challenger has the highest observed attributed rate.",
        "guardrail_failures": [],
        "lineage_ids": ["assignment-1", "assignment-2"],
        "evaluated_at": NOW,
    }
    value.update(overrides)
    return ExperimentEvaluation.model_validate(value)


def _proposal() -> JsonObject:
    prior_dossier = _prior_strategy().decision_dossier
    assert prior_dossier is not None
    return cast(
        "JsonObject",
        {
            "schema_version": "trace.outcome-reassessment-proposal.v1",
            "decision_dossier": {
                "schema_version": "trace.marketing-decision-dossier.v1",
                "situation": "experiment_result",
                "selected_icp_id": "ios-character-fans",
                "selection_basis_ids": ["signal-1"],
                "positioning": prior_dossier.positioning.model_dump(mode="json"),
                "evidence_dispositions": [
                    {
                        "evidence_id": "signal-1",
                        "disposition": "supports",
                        "confidence_basis_points": 7000,
                        "freshness": "fresh",
                        "use": "use_as_constraint",
                        "reason": "The approved audience signal remains current.",
                    },
                    {
                        "evidence_id": "evaluation-1",
                        "disposition": "supports",
                        "confidence_basis_points": 10000,
                        "freshness": "fresh",
                        "use": "use_as_constraint",
                        "reason": "The server-derived evaluation is the newest outcome signal.",
                    },
                ],
                "recommended_next_step": "design_experiment",
                "reason": "Revise one challenger assumption and preregister a follow-up.",
                "required_proof_ids": ["claim-1", "evaluation-1"],
            },
            "hypothesis_reassessments": [
                {
                    "hypothesis_id": "control",
                    "disposition": "retain",
                    "rationale": "The control remains the stable comparison.",
                    "next_test": "Keep it unchanged in the next complete block.",
                },
                {
                    "hypothesis_id": "challenger",
                    "disposition": "revise",
                    "rationale": "The outcome warrants a narrower replication.",
                    "next_test": "Change only the first value-frame sentence.",
                },
            ],
            "unanswered_questions": ["Will the direction replicate in another block?"],
        },
    )


def _task(evaluation: ExperimentEvaluation | None = None) -> MarketingTask:
    prior = _prior_strategy()
    observed = evaluation or _evaluation()
    return MarketingTask(
        task_id="reassessment-task-1",
        run_id="reassessment-run-1",
        account_id="trace_kr",
        kind=TaskKind.MARKETING_JUDGMENT,
        idempotency_key="outcome-reassessment:trace_kr:evaluation-1",
        payload=cast(
            "JsonObject",
            {
                "pipeline": "hosted_marketing_judgment_v1",
                "judgment": "outcome_reassessment",
                "reassessment_id": "reassessment-1",
                "campaign_id": "campaign-1",
                "account_id": "trace_kr",
                "situation": derive_reassessment_situation(observed, prior),
                "prior_strategy": prior.model_dump(mode="json"),
                "prior_strategy_sha256": contract_sha256(prior),
                "evaluation": observed.model_dump(mode="json"),
                "evaluation_sha256": contract_sha256(observed),
                "supported_claim_ids": ["claim-1"],
                "requested_by": "hosted_workspace",
            },
        ),
        created_at=NOW,
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


def test_live_evaluation_drives_a_bound_no_effect_reassessment(tmp_path: Path) -> None:
    codex = StubCodex(_proposal())
    executor = HostedOutcomeReassessmentExecutor(codex=codex, output_root=tmp_path)

    prepared = executor.prepare(_task())
    result = executor.execute(prepared)

    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["tool_actions_created"] == 0
    reassessment = result.output["reassessment"]
    assert isinstance(reassessment, dict)
    assert reassessment["situation"] == "experiment_result"
    assert "frozen outcome context" in codex.prompts[0]


def test_reassessment_situation_is_derived_instead_of_model_selected() -> None:
    prior = _prior_strategy()
    assert derive_reassessment_situation(_evaluation(), prior) == "experiment_result"
    assert (
        derive_reassessment_situation(_evaluation(winner_hypothesis_id="control"), prior)
        == "performance_regression"
    )
    stopped = _evaluation(
        state="stopped",
        winner_hypothesis_id=None,
        guardrail_failures=["publication_unknown_side_effect"],
    )
    assert derive_reassessment_situation(stopped, prior) == "tool_failure"


def test_reassessment_rejects_rewritten_evidence_before_returning_a_proposal(
    tmp_path: Path,
) -> None:
    proposal = _proposal()
    dossier = cast("dict[str, object]", proposal["decision_dossier"])
    dispositions = cast("list[dict[str, object]]", dossier["evidence_dispositions"])
    assert isinstance(dispositions, list)
    dispositions[0]["confidence_basis_points"] = 10000
    executor = HostedOutcomeReassessmentExecutor(
        codex=StubCodex(proposal),
        output_root=tmp_path,
    )

    with pytest.raises(MarketingExecutionError, match="outcome_reassessment_evidence_rewritten"):
        _ = executor.execute(executor.prepare(_task()))

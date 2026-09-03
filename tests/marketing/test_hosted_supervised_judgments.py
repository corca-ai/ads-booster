from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import TypeAdapter

from ads_booster.contracts.marketing_agent import (
    CreativeTreatment,
    ExperimentEvaluation,
    FeatureEvidencePacket,
    MediaPlan,
    StrategyBrief,
    contract_sha256,
)
from ads_booster.marketing.experiment_evaluation import (
    AssignmentObservation,
    ExperimentEvaluationRequest,
)
from ads_booster.marketing.hosted_candidate_judgment import HostedCandidateJudgmentExecutor
from ads_booster.marketing.hosted_experiment_evaluation import HostedExperimentEvaluationExecutor
from ads_booster.marketing.hosted_learning_judgment import HostedLearningJudgmentExecutor
from ads_booster.marketing.inbox import MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskStatus
from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 8, 31, tzinfo=UTC)
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_JSON_OBJECT_LIST: TypeAdapter[list[JsonObject]] = TypeAdapter(list[JsonObject])
_STRING_LIST: TypeAdapter[list[str]] = TypeAdapter(list[str])


def _digest(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _packet() -> FeatureEvidencePacket:
    return FeatureEvidencePacket.model_validate(
        {
            "schema_version": "trace.feature-evidence.v1",
            "packet_id": "packet-1",
            "feature_id": "trace.lockscreen.ai-concepts",
            "title": "AI lock screen concepts",
            "lifecycle": "installed_confirmed",
            "repository": "corca-ai/trace",
            "mutable_ref": "develop",
            "resolved_commit_sha": "a" * 40,
            "tree_sha": "b" * 40,
            "claims": [
                {
                    "claim_id": "claim-installed",
                    "text": "A character appears in scheduled lock-screen scenes.",
                    "status": "installed_confirmed",
                    "evidence_ids": ["runtime-1"],
                }
            ],
            "evidence": [
                {
                    "evidence_id": "runtime-1",
                    "kind": "runtime_observation",
                    "source_uri": "trace-install://receipt-1",
                    "immutable_ref": "install-1",
                    "content_sha256": "c" * 64,
                    "result": "observed",
                    "collected_at": NOW,
                }
            ],
            "limitations": [],
            "gate": {
                "publication_allowed": True,
                "allowed_claim_ids": ["claim-installed"],
                "blocked_claim_ids": [],
                "reasons": ["installed runtime observed"],
            },
            "observed_at": NOW,
        }
    )


def _registration(experiment_id: str = "experiment-1") -> JsonObject:
    return _JSON_OBJECT.validate_python(
        {
            "experiment_id": experiment_id,
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
            "stop_rules": ["guardrail failure"],
            "inconclusive_when": ["insufficient blocks"],
        }
    )


def _hypothesis(identifier: str, role: str) -> JsonObject:
    return _JSON_OBJECT.validate_python(
        {
            "hypothesis_id": identifier,
            "role": role,
            "claim_ids": ["claim-installed"],
            "value_frame": identifier,
            "rationale": f"rationale {identifier}",
            "falsifier": f"falsifier {identifier}",
            "proof_requirement": "Show the installed schedule.",
            "conversation_motive": "Ask which moment viewers want.",
            "reference_ids": [],
        }
    )


def _candidate_task() -> MarketingTask:
    packet = _packet()
    hypotheses = [_hypothesis("control", "control"), _hypothesis("challenger", "challenger")]
    strategy = StrategyBrief.model_validate(
        {
            "schema_version": "trace.strategy-brief.v1",
            "brief_id": "brief-1",
            "campaign_id": "campaign-1",
            "account_id": "trace_kr",
            "feature_packet_id": packet.packet_id,
            "feature_packet_sha256": contract_sha256(packet),
            "context_receipt_sha256": "1" * 64,
            "business_outcome": "Increase completed setups.",
            "audience_situation": "An iPhone user wants a character through the day.",
            "belief_to_change": "A lock screen can evolve through the day.",
            "hypotheses": hypotheses,
            "experiment": _registration(),
            "created_at": NOW,
        }
    )
    request = {
        "request_id": "request-challenger",
        "capability_id": "copy.text",
        "proof_kind": "copy_only",
        "claim_ids": ["claim-installed"],
        "instructions": "Materialize copy.",
    }
    treatment = CreativeTreatment.model_validate(
        {
            "treatment_id": "treatment-challenger",
            "hypothesis_id": "challenger",
            "format": "text_only",
            "hook": "A character lives through your day.",
            "caption_direction": "Show a day sequence.",
            "manipulated_component_value": "challenger",
            "proof_narrative": "Use installed evidence only.",
            "claim_ids": ["claim-installed"],
            "artifact_requests": [request],
        }
    )
    control_treatment = CreativeTreatment.model_validate(
        {
            **treatment.model_dump(mode="json"),
            "treatment_id": "treatment-control",
            "hypothesis_id": "control",
            "manipulated_component_value": "control",
            "artifact_requests": [{**request, "request_id": "request-control"}],
        }
    )
    plan = MediaPlan.model_validate(
        {
            "schema_version": "trace.media-plan.v1",
            "plan_id": "plan-1",
            "campaign_id": "campaign-1",
            "account_id": "trace_kr",
            "experiment_id": "experiment-1",
            "strategy_brief_sha256": contract_sha256(strategy),
            "context_receipt_sha256": "1" * 64,
            "treatments": [control_treatment, treatment],
            "publication_allowed": True,
            "human_review_required": True,
            "created_at": NOW,
        }
    )
    treatment_value = treatment.model_dump(mode="json")
    canonical_principles = ["Keep one situation and one belief change per post."]
    return MarketingTask(
        task_id="candidate-task-1",
        run_id="candidate-run-1",
        account_id="trace_kr",
        kind=TaskKind.MARKETING_JUDGMENT,
        idempotency_key="candidate-materialization:1",
        payload=_JSON_OBJECT.validate_python(
            {
                "pipeline": "hosted_marketing_judgment_v1",
                "judgment": "candidate_materialization",
                "campaign_id": "campaign-1",
                "assignment_id": "assignment-1",
                "eligible_block_id": "experiment-1.block-1",
                "allocation": {
                    "method": "balanced_complete_blocks",
                    "randomization_seed_sha256": None,
                    "rank": 0,
                    "posting_slot": None,
                },
                "feature_packet": packet.model_dump(mode="json"),
                "feature_packet_sha256": contract_sha256(packet),
                "strategy_brief": strategy.model_dump(mode="json"),
                "strategy_brief_sha256": contract_sha256(strategy),
                "media_plan": plan.model_dump(mode="json"),
                "media_plan_sha256": contract_sha256(plan),
                "treatment": treatment_value,
                "treatment_sha256": _digest(treatment_value),
                "account": {
                    "account_id": "trace_kr",
                    "country": "KR",
                    "language": "ko",
                    "timezone": "Asia/Seoul",
                },
                "canonical_principles": canonical_principles,
                "knowledge_snapshot_sha256": _digest({"principles": canonical_principles}),
                "requested_by": "hosted_workspace",
            }
        ),
        created_at=NOW,
    )


@dataclass(slots=True)
class StubJudgment:
    result: JsonObject

    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        _ = (prompt, schema)
        assert workspace.is_dir()
        assert timeout_seconds == 240
        return self.result


def _weekly_image_inputs() -> JsonObject:
    colors = ("2D936C", "00B4D8", "F9C74F", "F26419", "DA4C93")
    return _JSON_OBJECT.validate_python(
        {
            "trace_items": [
                {
                    "title": f"일정 {index + 1}",
                    "day": index % 7,
                    "days": 2 if index < 4 else 1,
                    "time": f"{7 + index:02d}:00" if index < 4 else None,
                    "color": colors[index % len(colors)],
                }
                for index in range(18)
            ],
            "trace_todos": [f"할 일 {index + 1}" for index in range(8)],
            "device_time": "09:41",
            "background_subject": "character_other",
            "background_mood": "warm",
            "background_search_query": None,
            "language": "ko",
        }
    )


def test_candidate_materializer_returns_one_bound_candidate_and_no_action(tmp_path: Path) -> None:
    proposal = _JSON_OBJECT.validate_python(
        {
            "schema_version": "trace.candidate-materialization.v2",
            "topic": "A character's day",
            "country": "KR",
            "caption": "내 최애가 아침부터 밤까지 잠금화면에서 하루를 보낸다면?",
            "hypothesis": "Character continuity may improve attributed setup completion.",
            "posting_slot": "morning",
            "appium_prompt": "Capture the installed schedule.",
            "image_inputs": _weekly_image_inputs(),
            "claim_ids": ["claim-installed"],
        }
    )
    executor = HostedCandidateJudgmentExecutor(
        codex=StubJudgment(proposal),
        output_root=tmp_path,
    )

    prepared = executor.prepare(_candidate_task())
    definitions = _JSON_OBJECT.validate_python(prepared.schema["$defs"])
    image_schema = _JSON_OBJECT.validate_python(definitions["CandidateImageInputs"])
    image_properties = _JSON_OBJECT.validate_python(image_schema["properties"])
    trace_items = _JSON_OBJECT.validate_python(image_properties["trace_items"])
    trace_todos = _JSON_OBJECT.validate_python(image_properties["trace_todos"])
    assert (trace_items["minItems"], trace_items["maxItems"]) == (18, 22)
    assert (trace_todos["minItems"], trace_todos["maxItems"]) == (8, 12)
    assert "background_intent" not in image_properties
    entry_schema = _JSON_OBJECT.validate_python(definitions["CandidateScheduleEntry"])
    assert entry_schema["required"] == ["title", "day", "days", "time", "color"]

    result = executor.execute(prepared)

    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["tool_actions_created"] == 0
    assert result.output["candidate"] == proposal
    assert result.output["candidate_sha256"] == _digest(proposal)


def test_candidate_materializer_hashes_the_trimmed_callback_wire_value(tmp_path: Path) -> None:
    image_inputs = _JSON_OBJECT.validate_python(_weekly_image_inputs())
    trace_items = _JSON_OBJECT_LIST.validate_python(image_inputs["trace_items"])
    trace_items[0]["title"] = "  일정 1  "
    trace_todos = _STRING_LIST.validate_python(image_inputs["trace_todos"])
    trace_todos[0] = "  할 일 1  "
    image_inputs["trace_items"] = cast("JsonValue", trace_items)
    image_inputs["trace_todos"] = cast("JsonValue", trace_todos)
    image_inputs["background_mood"] = "  warm  "
    image_inputs["background_search_query"] = "  cozy room  "
    proposal = {
        "schema_version": "trace.candidate-materialization.v2",
        "topic": "  A character's day  ",
        "country": "KR",
        "caption": "  잠금화면에서 함께 보내는 하루  ",
        "hypothesis": "  Continuity may improve setup completion.  ",
        "posting_slot": "morning",
        "appium_prompt": "Capture the installed schedule.",
        "image_inputs": image_inputs,
        "claim_ids": ["claim-installed"],
    }
    executor = HostedCandidateJudgmentExecutor(
        codex=StubJudgment(_JSON_OBJECT.validate_python(proposal)),
        output_root=tmp_path,
    )

    result = executor.execute(executor.prepare(_candidate_task()))

    candidate = _JSON_OBJECT.validate_python(result.output["candidate"])
    normalized_image = _JSON_OBJECT.validate_python(candidate["image_inputs"])
    normalized_items = _JSON_OBJECT_LIST.validate_python(normalized_image["trace_items"])
    normalized_todos = _STRING_LIST.validate_python(normalized_image["trace_todos"])
    assert candidate["topic"] == "A character's day"
    assert candidate["caption"] == "잠금화면에서 함께 보내는 하루"
    assert candidate["hypothesis"] == "Continuity may improve setup completion."
    assert normalized_items[0]["title"] == "일정 1"
    assert normalized_todos[0] == "할 일 1"
    assert normalized_image["background_mood"] == "warm"
    assert normalized_image["background_search_query"] == "cozy room"
    assert result.output["candidate_sha256"] == _digest(candidate)


def test_candidate_materializer_rejects_the_legacy_day_zero_schedule(tmp_path: Path) -> None:
    proposal = _JSON_OBJECT.validate_python(
        {
            "schema_version": "trace.candidate-materialization.v2",
            "topic": "A character's day",
            "country": "KR",
            "caption": "내 최애가 하루를 함께 보낸다면?",
            "hypothesis": "A weekly scene may improve setup completion.",
            "posting_slot": "morning",
            "appium_prompt": "Capture the installed schedule.",
            "image_inputs": {
                **_weekly_image_inputs(),
                "trace_items": [
                    "07:00 Wake up",
                    "09:00 Work",
                    "12:00 Lunch",
                    "18:00 Commute",
                    "22:00 Sleep",
                ],
            },
            "claim_ids": ["claim-installed"],
        }
    )
    executor = HostedCandidateJudgmentExecutor(
        codex=StubJudgment(proposal),
        output_root=tmp_path,
    )

    with pytest.raises(MarketingExecutionError) as captured:
        _ = executor.execute(executor.prepare(_candidate_task()))

    assert captured.value.failure_code == "candidate_judgment_result_invalid"


def _evaluation(campaign_id: str, experiment_id: str, winner: str) -> ExperimentEvaluation:
    return ExperimentEvaluation.model_validate(
        {
            "schema_version": "trace.experiment-evaluation.v1",
            "evaluation_id": f"evaluation-{campaign_id}",
            "campaign_id": campaign_id,
            "experiment_id": experiment_id,
            "state": "evaluated",
            "winner_hypothesis_id": winner,
            "outcome_scope": "direct_response_attribution",
            "eligible_blocks": 2,
            "attribution_coverage_basis_points": 10000,
            "guardrail_failures": [],
            "interpretation": "Direct-response direction; not a causal effect.",
            "lineage_ids": [f"lineage-{campaign_id}"],
            "evaluated_at": NOW,
        }
    )


def test_learning_synthesis_accepts_independent_semantic_replication(tmp_path: Path) -> None:
    evaluations = (
        _evaluation("campaign-1", "experiment-1", "challenger-1"),
        _evaluation("campaign-2", "experiment-2", "challenger-2"),
    )
    lineages = [
        {
            "evaluation": evaluation.model_dump(mode="json"),
            "winner_hypothesis": {"hypothesis_id": evaluation.winner_hypothesis_id},
            "winner_treatment": {
                "treatment_id": f"treatment-{index}",
                "hypothesis_id": evaluation.winner_hypothesis_id,
            },
        }
        for index, evaluation in enumerate(evaluations, start=1)
    ]
    task = MarketingTask(
        task_id="learning-task-1",
        run_id="learning-run-1",
        account_id="trace_kr",
        kind=TaskKind.MARKETING_JUDGMENT,
        idempotency_key="learning-synthesis:1",
        payload=_JSON_OBJECT.validate_python(
            {
                "pipeline": "hosted_marketing_judgment_v1",
                "judgment": "learning_synthesis",
                "learning_id": "learning-1",
                "target_campaign_id": "campaign-2",
                "account_id": "trace_kr",
                "applicability": {
                    "schema_version": "trace.marketing-learning-applicability.v1",
                    "account_id": "trace_kr",
                    "feature_id": "trace.lockscreen.ai-concepts",
                    "feature_packet_sha256": "a" * 64,
                    "country": "KR",
                    "language": "ko",
                    "mode": "assisted",
                    "marketing_context_snapshot_sha256": None,
                },
                "lineages": lineages,
                "requested_by": "hosted_workspace",
            }
        ),
        created_at=NOW,
    )
    executor = HostedLearningJudgmentExecutor(
        codex=StubJudgment(
            _JSON_OBJECT.validate_python(
                {
                    "schema_version": "trace.learning-synthesis.v1",
                    "statement": "Character-day framing may improve attributed setup completion.",
                    "scope": "KR iPhone installed-evidence campaigns",
                    "limitations": ["Direct response is not a causal effect."],
                }
            )
        ),
        output_root=tmp_path,
    )

    result = executor.execute(executor.prepare(task))

    assert result.status is TaskStatus.SUCCEEDED
    candidate = result.output["learning_candidate"]
    assert isinstance(candidate, dict)
    assert candidate["independent_lineage_ids"] == [
        "evaluation-campaign-1",
        "evaluation-campaign-2",
    ]
    assert candidate["applicability"] == {
        "schema_version": "trace.marketing-learning-applicability.v1",
        "account_id": "trace_kr",
        "feature_id": "trace.lockscreen.ai-concepts",
        "feature_packet_sha256": "a" * 64,
        "country": "KR",
        "language": "ko",
        "mode": "assisted",
        "marketing_context_snapshot_sha256": None,
    }
    assert result.output["tool_actions_created"] == 0


def test_experiment_evaluation_executor_is_deterministic_and_admitted() -> None:
    request = ExperimentEvaluationRequest(
        evaluation_id="evaluation-1",
        campaign_id="campaign-1",
        registration=StrategyBrief.model_validate(
            _candidate_task().payload["strategy_brief"]
        ).experiment,
        observations=(
            AssignmentObservation(
                assignment_id="assignment-control",
                eligible_block_id="block-1",
                hypothesis_id="control",
                publication_id="publication-control",
                eligible=True,
                attribution_observed=True,
                converted=False,
            ),
            AssignmentObservation(
                assignment_id="assignment-challenger",
                eligible_block_id="block-1",
                hypothesis_id="challenger",
                publication_id="publication-challenger",
                product_event_id="event-1",
                eligible=True,
                attribution_observed=True,
                converted=True,
            ),
        ),
        windows_complete=True,
        evaluated_at=NOW,
    )
    task = MarketingTask(
        task_id="evaluation-task-1",
        run_id="evaluation-run-1",
        account_id="trace_kr",
        kind=TaskKind.MARKETING_JUDGMENT,
        idempotency_key="experiment-evaluation:1",
        payload=_JSON_OBJECT.validate_python(
            {
                "pipeline": "hosted_marketing_judgment_v1",
                "judgment": "experiment_evaluation",
                "account_id": "trace_kr",
                "request": request.model_dump(mode="json"),
                "requested_by": "hosted_workspace",
            }
        ),
        created_at=NOW,
    )
    executor = HostedExperimentEvaluationExecutor()

    prepared = executor.prepare(task)
    result = executor.execute(prepared)

    assert prepared.execution_admission.workspace_id.startswith(
        "deterministic-experiment-evaluation:"
    )
    assert result.status is TaskStatus.SUCCEEDED
    assert result.output["tool_actions_created"] == 0

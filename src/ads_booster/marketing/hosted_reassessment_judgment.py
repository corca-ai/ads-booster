"""Outcome-triggered, no-effect marketing reassessment for hosted campaigns."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from ads_booster.contracts.marketing_agent import (
    DecisionDossier,
    ExperimentEvaluation,
    HypothesisReassessment,
    MarketingReassessment,
    StrategyBrief,
    contract_sha256,
)
from ads_booster.marketing.inbox import ExecutionAdmission, MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult, TaskStatus
from ads_booster.providers.codex_cli import CodexCliError
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

PIPELINE: Final = "hosted_marketing_judgment_v1"
JUDGMENT: Final = "outcome_reassessment"
_WORKSPACE_DIRECTORY: Final = "codex-outcome-reassessment"
_DEFAULT_TIMEOUT_SECONDS: Final = 240.0
_VERIFIED_EVALUATION_CONFIDENCE: Final = 10_000
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)

ReassessmentSituation = Literal[
    "experiment_result",
    "performance_regression",
    "tool_failure",
]


class ReassessmentModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class OutcomeReassessmentRequest(ReassessmentModel):
    pipeline: Literal["hosted_marketing_judgment_v1"]
    judgment: Literal["outcome_reassessment"]
    reassessment_id: Annotated[str, Field(min_length=1, max_length=128)]
    campaign_id: Annotated[str, Field(min_length=1, max_length=128)]
    account_id: Annotated[str, Field(min_length=1, max_length=128)]
    situation: ReassessmentSituation
    prior_strategy: StrategyBrief
    prior_strategy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluation: ExperimentEvaluation
    evaluation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    supported_claim_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=64)]
    requested_by: Literal["hosted_workspace"]

    @model_validator(mode="after")
    def validate_lineage(self) -> OutcomeReassessmentRequest:
        if self.prior_strategy.decision_dossier is None:
            raise ValueError("outcome reassessment requires a decision dossier")
        if contract_sha256(self.prior_strategy) != self.prior_strategy_sha256:
            raise ValueError("prior strategy digest does not match its frozen payload")
        if contract_sha256(self.evaluation) != self.evaluation_sha256:
            raise ValueError("evaluation digest does not match its frozen payload")
        if (
            self.campaign_id != self.prior_strategy.campaign_id
            or self.campaign_id != self.evaluation.campaign_id
            or self.evaluation.experiment_id != self.prior_strategy.experiment.experiment_id
        ):
            raise ValueError("reassessment lineage is not bound to one campaign experiment")
        if self.account_id != self.prior_strategy.account_id:
            raise ValueError("reassessment account does not match its prior strategy")
        expected_situation = derive_reassessment_situation(
            self.evaluation,
            self.prior_strategy,
        )
        if self.situation != expected_situation:
            raise ValueError("reassessment situation was not derived from the evaluation")
        strategy_claim_ids = {
            claim_id
            for hypothesis in self.prior_strategy.hypotheses
            for claim_id in hypothesis.claim_ids
        }
        if len(set(self.supported_claim_ids)) != len(self.supported_claim_ids):
            raise ValueError("supported claim IDs must be unique")
        if not strategy_claim_ids.issubset(self.supported_claim_ids):
            raise ValueError("prior strategy claims must remain supported")
        return self


class OutcomeReassessmentProposal(ReassessmentModel):
    schema_version: Literal["trace.outcome-reassessment-proposal.v1"]
    decision_dossier: DecisionDossier
    hypothesis_reassessments: Annotated[
        tuple[HypothesisReassessment, ...],
        Field(min_length=2, max_length=8),
    ]
    unanswered_questions: Annotated[tuple[str, ...], Field(max_length=16)] = ()


class StructuredReassessmentJudgment(Protocol):
    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class PreparedOutcomeReassessment:
    request: OutcomeReassessmentRequest
    prompt: str
    schema: JsonObject
    execution_admission: ExecutionAdmission
    workspace: Path


@dataclass(frozen=True, slots=True)
class HostedOutcomeReassessmentExecutor:
    codex: StructuredReassessmentJudgment
    output_root: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def prepare(self, task: MarketingTask) -> PreparedOutcomeReassessment:
        if task.kind is not TaskKind.MARKETING_JUDGMENT:
            raise MarketingExecutionError("unsupported_outcome_reassessment_task")
        try:
            request = OutcomeReassessmentRequest.model_validate(task.payload)
        except ValidationError as error:
            raise MarketingExecutionError("outcome_reassessment_payload_invalid") from error
        if request.account_id != task.account_id:
            raise MarketingExecutionError("outcome_reassessment_scope_invalid")
        schema = _JSON_OBJECT.validate_python(OutcomeReassessmentProposal.model_json_schema())
        prompt = _reassessment_prompt(request)
        task_digest = sha256(task.model_dump_json().encode()).hexdigest()
        root = self.output_root.resolve()
        workspace = (root / _WORKSPACE_DIRECTORY / task_digest).resolve()
        if not workspace.is_relative_to(root):
            raise MarketingExecutionError("outcome_reassessment_workspace_invalid")
        try:
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            workspace.chmod(0o700)
        except OSError as error:
            raise MarketingExecutionError("outcome_reassessment_workspace_unavailable") from error
        return PreparedOutcomeReassessment(
            request=request,
            prompt=prompt,
            schema=schema,
            execution_admission=ExecutionAdmission(
                job_digest=task_digest,
                export_nonce=secrets.token_hex(32),
                workspace_id=f"codex-outcome-reassessment:{task_digest}",
            ),
            workspace=workspace,
        )

    def execute(self, prepared: PreparedOutcomeReassessment) -> TaskResult:
        try:
            raw = self.codex.run_marketing_judgment_job(
                prepared.prompt,
                prepared.schema,
                workspace=prepared.workspace,
                timeout_seconds=self.timeout_seconds,
            )
            proposal = OutcomeReassessmentProposal.model_validate(raw)
            _validate_proposal(prepared.request, proposal)
            reassessment = MarketingReassessment(
                schema_version="trace.marketing-reassessment.v1",
                reassessment_id=prepared.request.reassessment_id,
                campaign_id=prepared.request.campaign_id,
                trigger_evaluation_id=prepared.request.evaluation.evaluation_id,
                trigger_evaluation_sha256=prepared.request.evaluation_sha256,
                situation=prepared.request.situation,
                decision_dossier=proposal.decision_dossier,
                hypothesis_reassessments=proposal.hypothesis_reassessments,
                unanswered_questions=proposal.unanswered_questions,
                created_at=prepared.request.evaluation.evaluated_at,
            )
        except (CodexCliError, ValidationError) as error:
            raise MarketingExecutionError(
                "outcome_reassessment_result_invalid",
                unknown_side_effect=True,
            ) from error
        except MarketingExecutionError:
            raise
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "pipeline": PIPELINE,
                "judgment": JUDGMENT,
                "reassessment": _JSON_OBJECT.validate_python(reassessment.model_dump(mode="json")),
                "reassessment_sha256": contract_sha256(reassessment),
                "tool_actions_created": 0,
            },
        )


def derive_reassessment_situation(
    evaluation: ExperimentEvaluation,
    prior_strategy: StrategyBrief,
) -> ReassessmentSituation:
    """Route observed outcome state; the model still decides the marketing response."""
    if any("unknown_side_effect" in failure for failure in evaluation.guardrail_failures):
        return "tool_failure"
    control_id = next(
        hypothesis.hypothesis_id
        for hypothesis in prior_strategy.hypotheses
        if hypothesis.role.value == "control"
    )
    if evaluation.state == "stopped" or evaluation.winner_hypothesis_id == control_id:
        return "performance_regression"
    return "experiment_result"


def _validate_proposal(
    request: OutcomeReassessmentRequest,
    proposal: OutcomeReassessmentProposal,
) -> None:
    dossier = proposal.decision_dossier
    _validate_decision_scope(request, dossier)
    required_evidence_ids = _validate_evidence_dispositions(request, dossier)
    allowed_proof_ids = set(request.supported_claim_ids) | required_evidence_ids
    if not set(dossier.required_proof_ids).issubset(allowed_proof_ids):
        raise MarketingExecutionError(
            "outcome_reassessment_required_proof_unbound",
            unknown_side_effect=True,
        )
    _validate_hypothesis_reassessments(request, proposal)


def _validate_decision_scope(
    request: OutcomeReassessmentRequest,
    dossier: DecisionDossier,
) -> None:
    prior_dossier = _prior_dossier(request)
    if dossier.situation != request.situation:
        raise MarketingExecutionError(
            "outcome_reassessment_situation_changed",
            unknown_side_effect=True,
        )
    if dossier.selected_icp_id not in {
        prior_dossier.selected_icp_id,
        "research_needed",
    }:
        raise MarketingExecutionError(
            "outcome_reassessment_icp_invented",
            unknown_side_effect=True,
        )
    if not set(dossier.positioning.proof_claim_ids).issubset(request.supported_claim_ids):
        raise MarketingExecutionError(
            "outcome_reassessment_claim_unsupported",
            unknown_side_effect=True,
        )


def _validate_evidence_dispositions(
    request: OutcomeReassessmentRequest,
    dossier: DecisionDossier,
) -> set[str]:
    prior_dossier = _prior_dossier(request)
    prior_dispositions = {item.evidence_id: item for item in prior_dossier.evidence_dispositions}
    dispositions = {item.evidence_id: item for item in dossier.evidence_dispositions}
    required_evidence_ids = {*prior_dispositions, request.evaluation.evaluation_id}
    if set(dispositions) != required_evidence_ids:
        raise MarketingExecutionError(
            "outcome_reassessment_evidence_incomplete",
            unknown_side_effect=True,
        )
    for evidence_id, prior in prior_dispositions.items():
        current = dispositions[evidence_id]
        if (
            current.freshness != prior.freshness
            or current.confidence_basis_points != prior.confidence_basis_points
        ):
            raise MarketingExecutionError(
                "outcome_reassessment_evidence_rewritten",
                unknown_side_effect=True,
            )
    evaluation_disposition = dispositions[request.evaluation.evaluation_id]
    if (
        evaluation_disposition.freshness != "fresh"
        or evaluation_disposition.confidence_basis_points != _VERIFIED_EVALUATION_CONFIDENCE
        or evaluation_disposition.use == "exclude"
    ):
        raise MarketingExecutionError(
            "outcome_reassessment_evaluation_rewritten",
            unknown_side_effect=True,
        )
    if not set(dossier.selection_basis_ids).issubset(required_evidence_ids):
        raise MarketingExecutionError(
            "outcome_reassessment_icp_basis_unbound",
            unknown_side_effect=True,
        )
    prior_icp = prior_dossier.selected_icp_id
    if dossier.selected_icp_id == prior_icp and prior_icp != "research_needed":
        prior_basis = set(prior_dossier.selection_basis_ids)
        if not prior_basis.intersection(dossier.selection_basis_ids):
            raise MarketingExecutionError(
                "outcome_reassessment_icp_basis_unbound",
                unknown_side_effect=True,
            )
    return required_evidence_ids


def _prior_dossier(request: OutcomeReassessmentRequest) -> DecisionDossier:
    dossier = request.prior_strategy.decision_dossier
    if dossier is None:
        raise MarketingExecutionError("outcome_reassessment_payload_invalid")
    return dossier


def _validate_hypothesis_reassessments(
    request: OutcomeReassessmentRequest,
    proposal: OutcomeReassessmentProposal,
) -> None:
    expected_hypotheses = {
        hypothesis.hypothesis_id for hypothesis in request.prior_strategy.hypotheses
    }
    supplied_hypotheses = {item.hypothesis_id for item in proposal.hypothesis_reassessments}
    if supplied_hypotheses != expected_hypotheses:
        raise MarketingExecutionError(
            "outcome_reassessment_hypotheses_incomplete",
            unknown_side_effect=True,
        )
    if request.situation == "tool_failure" and any(
        item.disposition != "retain" or item.next_test is not None
        for item in proposal.hypothesis_reassessments
    ):
        raise MarketingExecutionError(
            "outcome_reassessment_before_effect_reconciliation",
            unknown_side_effect=True,
        )


def _reassessment_prompt(request: OutcomeReassessmentRequest) -> str:
    payload = json.dumps(
        {
            "situation": request.situation,
            "supported_claim_ids": request.supported_claim_ids,
            "prior_strategy": request.prior_strategy.model_dump(mode="json"),
            "evaluation": request.evaluation.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "당신은 Trace Threads 마케팅 에이전트의 outcome strategist다. 사전 등록된 실험의 "
        "실제 평가를 이전 전략과 비교해 다음 판단을 제안한다. 게시·예산·도구 실행 권한은 "
        "없으며 결과는 사람에게 보여 줄 no-effect 제안이다.\n\n"
        "규칙:\n"
        "1. evaluation과 prior_strategy를 ground truth로 삼고 결과에 맞춰 가설별 retain, revise, "
        "retire를 독립적으로 판단한다.\n"
        "2. 평가가 inconclusive이면 승패를 발명하지 말고 정보 부족을 명시한다.\n"
        "3. direct-response attribution은 causal effect로 표현하지 않는다.\n"
        "4. prior evidence의 freshness와 confidence는 바꾸지 않는다. evaluation 자체는 fresh, "
        "confidence 10000으로 모두 disposition한다.\n"
        "5. 기존 ICP를 유지하거나 research_needed로 되돌릴 수만 있다. 새로운 ICP를 발명하지 "
        "않는다.\n"
        "6. tool_failure이면 효과 reconciliation 외의 다음 행동을 제안하지 않고 모든 가설을 "
        "retain하며 next_test를 비운다.\n"
        "7. 모든 기존 hypothesis를 정확히 한 번 검토하고, 고정 포맷 답이 아니라 관측 결과가 "
        "바꾼 가정과 다음 검증 질문을 구체적으로 적는다.\n\n"
        f"frozen outcome context: {payload}\n"
    )

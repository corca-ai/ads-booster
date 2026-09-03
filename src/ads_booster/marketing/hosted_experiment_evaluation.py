"""Deterministic hosted experiment evaluation without a model turn."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ads_booster.contracts.marketing_agent import contract_sha256
from ads_booster.marketing.experiment_evaluation import (
    ExperimentEvaluationRequest,
    evaluate_experiment,
)
from ads_booster.marketing.inbox import ExecutionAdmission, MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult, TaskStatus

PIPELINE: Final = "hosted_marketing_judgment_v1"
JUDGMENT: Final = "experiment_evaluation"


class EvaluationTaskPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    pipeline: Literal["hosted_marketing_judgment_v1"]
    judgment: Literal["experiment_evaluation"]
    account_id: Annotated[str, Field(min_length=1, max_length=128)]
    request: ExperimentEvaluationRequest
    requested_by: Literal["hosted_workspace"]


@dataclass(frozen=True, slots=True)
class PreparedExperimentEvaluation:
    payload: EvaluationTaskPayload
    execution_admission: ExecutionAdmission


@dataclass(frozen=True, slots=True)
class HostedExperimentEvaluationExecutor:
    def prepare(self, task: MarketingTask) -> PreparedExperimentEvaluation:
        if task.kind is not TaskKind.MARKETING_JUDGMENT:
            raise MarketingExecutionError("unsupported_experiment_evaluation_task")
        try:
            payload = EvaluationTaskPayload.model_validate(task.payload)
        except ValidationError as error:
            raise MarketingExecutionError("experiment_evaluation_payload_invalid") from error
        if payload.account_id != task.account_id:
            raise MarketingExecutionError("experiment_evaluation_scope_invalid")
        task_digest = sha256(task.model_dump_json().encode()).hexdigest()
        return PreparedExperimentEvaluation(
            payload=payload,
            execution_admission=ExecutionAdmission(
                job_digest=task_digest,
                export_nonce=secrets.token_hex(32),
                workspace_id=f"deterministic-experiment-evaluation:{task_digest}",
            ),
        )

    def execute(self, prepared: PreparedExperimentEvaluation) -> TaskResult:
        try:
            evaluation = evaluate_experiment(prepared.payload.request)
        except (ValueError, ValidationError) as error:
            raise MarketingExecutionError("experiment_evaluation_invalid") from error
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "pipeline": PIPELINE,
                "judgment": JUDGMENT,
                "evaluation": evaluation.model_dump(mode="json"),
                "evaluation_sha256": contract_sha256(evaluation),
                "tool_actions_created": 0,
            },
        )

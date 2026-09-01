"""Synthesize a reversible learning candidate from replicated experiment lineages."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from ads_booster.contracts.marketing_agent import (
    ExperimentEvaluation,
    LearningCandidate,
    contract_sha256,
)
from ads_booster.marketing.inbox import ExecutionAdmission, MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult, TaskStatus
from ads_booster.providers.codex_cli import CodexCliError
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

PIPELINE: Final = "hosted_marketing_judgment_v1"
JUDGMENT: Final = "learning_synthesis"
_SCHEMA_VERSION: Final = "trace.learning-synthesis.v1"
_WORKSPACE_DIRECTORY: Final = "codex-learning-synthesis"
_DEFAULT_TIMEOUT_SECONDS: Final = 240.0
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class LearningModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class LearningLineage(LearningModel):
    evaluation: ExperimentEvaluation
    winner_hypothesis: JsonObject
    winner_treatment: JsonObject


class LearningSynthesisRequest(LearningModel):
    pipeline: Literal["hosted_marketing_judgment_v1"]
    judgment: Literal["learning_synthesis"]
    learning_id: Annotated[str, Field(min_length=1, max_length=128)]
    target_campaign_id: Annotated[str, Field(min_length=1, max_length=128)]
    account_id: Annotated[str, Field(min_length=1, max_length=128)]
    lineages: Annotated[tuple[LearningLineage, ...], Field(min_length=2, max_length=32)]
    requested_by: Literal["hosted_workspace"]

    @model_validator(mode="after")
    def validate_replication(self) -> LearningSynthesisRequest:
        evaluations = [item.evaluation for item in self.lineages]
        if any(
            item.state != "evaluated" or item.winner_hypothesis_id is None for item in evaluations
        ):
            raise ValueError("learning synthesis requires evaluated winner lineages")
        if len({item.campaign_id for item in evaluations}) != len(evaluations):
            raise ValueError("learning synthesis requires independent campaigns")
        for lineage in self.lineages:
            if lineage.winner_hypothesis.get("hypothesis_id") != (
                lineage.evaluation.winner_hypothesis_id
            ):
                raise ValueError("learning winner hypothesis binding is invalid")
            if lineage.winner_treatment.get("hypothesis_id") != (
                lineage.evaluation.winner_hypothesis_id
            ):
                raise ValueError("learning winner treatment binding is invalid")
        return self


class LearningSynthesisProposal(LearningModel):
    schema_version: Literal["trace.learning-synthesis.v1"]
    statement: Annotated[str, Field(min_length=1, max_length=2000)]
    scope: Annotated[str, Field(min_length=1, max_length=500)]
    limitations: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]


class StructuredLearningJudgment(Protocol):
    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class PreparedLearningJudgment:
    request: LearningSynthesisRequest
    prompt: str
    schema: JsonObject
    admission: ExecutionAdmission
    workspace: Path

    @property
    def execution_admission(self) -> ExecutionAdmission:
        return self.admission


@dataclass(frozen=True, slots=True)
class HostedLearningJudgmentExecutor:
    codex: StructuredLearningJudgment
    output_root: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def prepare(self, task: MarketingTask) -> PreparedLearningJudgment:
        if task.kind is not TaskKind.MARKETING_JUDGMENT:
            raise MarketingExecutionError("unsupported_learning_judgment_task")
        try:
            request = LearningSynthesisRequest.model_validate(task.payload)
        except ValidationError as error:
            raise MarketingExecutionError("learning_judgment_payload_invalid") from error
        if request.account_id != task.account_id:
            raise MarketingExecutionError("learning_judgment_scope_mismatch")
        schema = _JSON_OBJECT.validate_python(LearningSynthesisProposal.model_json_schema())
        prompt = _learning_prompt(request)
        request_digest = sha256(task.model_dump_json().encode()).hexdigest()
        root = self.output_root.resolve()
        workspace = (root / _WORKSPACE_DIRECTORY / request_digest).resolve()
        if not workspace.is_relative_to(root):
            raise MarketingExecutionError("learning_judgment_workspace_invalid")
        try:
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            workspace.chmod(0o700)
        except OSError as error:
            raise MarketingExecutionError("learning_judgment_workspace_unavailable") from error
        return PreparedLearningJudgment(
            request=request,
            prompt=prompt,
            schema=schema,
            admission=ExecutionAdmission(
                job_digest=request_digest,
                export_nonce=secrets.token_hex(32),
                workspace_id=f"codex-learning-judgment:{request_digest}",
            ),
            workspace=workspace,
        )

    def execute(self, prepared: PreparedLearningJudgment) -> TaskResult:
        try:
            raw = self.codex.run_marketing_judgment_job(
                prepared.prompt,
                prepared.schema,
                workspace=prepared.workspace,
                timeout_seconds=self.timeout_seconds,
            )
            proposal = LearningSynthesisProposal.model_validate(raw)
            candidate = LearningCandidate(
                schema_version="trace.learning-candidate.v1",
                learning_id=prepared.request.learning_id,
                campaign_id=prepared.request.target_campaign_id,
                statement=proposal.statement,
                scope=proposal.scope,
                independent_lineage_ids=tuple(
                    item.evaluation.evaluation_id for item in prepared.request.lineages
                ),
                status="candidate",
                created_at=prepared.request.lineages[-1].evaluation.evaluated_at,
            )
        except (CodexCliError, ValidationError) as error:
            raise MarketingExecutionError(
                "learning_judgment_result_invalid",
                unknown_side_effect=True,
            ) from error
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "pipeline": PIPELINE,
                "judgment": JUDGMENT,
                "learning_candidate": _JSON_OBJECT.validate_python(
                    candidate.model_dump(mode="json")
                ),
                "learning_candidate_sha256": contract_sha256(candidate),
                "limitations": list(proposal.limitations),
                "tool_actions_created": 0,
            },
        )


def _learning_prompt(request: LearningSynthesisRequest) -> str:
    payload = json.dumps(
        [item.model_dump(mode="json") for item in request.lineages],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "당신은 Trace Threads 마케팅 에이전트의 learning governor다. 독립 캠페인의 "
        "descriptive attribution 결과를 비교해 재사용 가능한 학습 후보를 제안한다. 이것은 "
        "원칙 승격이 아니라 사람 검수 대상이며, 인과 효과라고 표현하지 않는다.\n\n"
        "규칙:\n"
        "1. 모든 평가가 보여준 공통 방향만 statement에 쓰고 차이를 숨기지 않는다.\n"
        "2. scope를 계정·시장·기능·proof 조건에 맞게 좁힌다.\n"
        "3. 최소 한 개 limitations를 명시한다.\n"
        "4. 단일 게시물 일반화, causal claim, 자동 게시 지시는 금지한다.\n\n"
        f"replicated lineages: {payload}\n"
    )

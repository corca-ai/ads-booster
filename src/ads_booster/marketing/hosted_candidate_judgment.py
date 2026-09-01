"""Materialize one evidence-bound candidate from an approved marketing treatment."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from ads_booster.contracts.marketing_agent import (
    ContextReceipt,
    CreativeTreatment,
    FeatureEvidencePacket,
    MediaPlan,
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
JUDGMENT: Final = "candidate_materialization"
_PROMPT_VERSION: Final = "trace.evidence-bound-candidate.v1"
_SCHEMA_VERSION: Final = "trace.candidate-materialization.v1"
_WORKSPACE_DIRECTORY: Final = "codex-candidate-materialization"
_DEFAULT_TIMEOUT_SECONDS: Final = 240.0
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class CandidateModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CandidateAccount(CandidateModel):
    account_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")]
    timezone: Annotated[str, Field(min_length=1, max_length=100)]


class CandidateImageInputs(CandidateModel):
    trace_items: tuple[
        Annotated[
            str,
            Field(min_length=7, max_length=80, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d\s+.+$"),
        ],
        ...,
    ] = Field(min_length=5, max_length=8)
    device_time: Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]
    background_subject: Literal[
        "scenery",
        "character_kitty",
        "character_other",
        "family_photo",
        "person",
        "pet",
        "minimal",
        "sports_team",
        "none",
    ]
    background_mood: Annotated[str, Field(min_length=1, max_length=40)]
    background_search_query: Annotated[str | None, Field(max_length=200)] = None
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")]


class CandidateProposal(CandidateModel):
    schema_version: Literal["trace.candidate-materialization.v1"]
    topic: Annotated[str, Field(min_length=1, max_length=200)]
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    caption: Annotated[str, Field(min_length=1, max_length=10_000)]
    hypothesis: Annotated[str, Field(min_length=1, max_length=2_000)]
    posting_slot: Literal["morning", "evening", "manual"]
    appium_prompt: Annotated[str, Field(max_length=10_000)] = ""
    image_inputs: CandidateImageInputs
    claim_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]


class CandidateMaterializationRequest(CandidateModel):
    pipeline: Literal["hosted_marketing_judgment_v1"]
    judgment: Literal["candidate_materialization"]
    campaign_id: Annotated[str, Field(min_length=1, max_length=128)]
    assignment_id: Annotated[str, Field(min_length=1, max_length=128)]
    eligible_block_id: Annotated[str, Field(min_length=1, max_length=128)]
    feature_packet: FeatureEvidencePacket
    feature_packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_brief: StrategyBrief
    strategy_brief_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_plan: MediaPlan
    media_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    treatment: CreativeTreatment
    treatment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    account: CandidateAccount
    knowledge_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    requested_by: Literal["hosted_workspace"]

    @model_validator(mode="after")
    def validate_bindings(self) -> CandidateMaterializationRequest:
        if contract_sha256(self.feature_packet) != self.feature_packet_sha256:
            raise ValueError("feature packet digest mismatch")
        if contract_sha256(self.strategy_brief) != self.strategy_brief_sha256:
            raise ValueError("strategy brief digest mismatch")
        if contract_sha256(self.media_plan) != self.media_plan_sha256:
            raise ValueError("media plan digest mismatch")
        if _json_sha256(self.treatment.model_dump(mode="json")) != self.treatment_sha256:
            raise ValueError("treatment digest mismatch")
        if (
            self.strategy_brief.campaign_id != self.campaign_id
            or self.media_plan.campaign_id != self.campaign_id
            or self.strategy_brief.account_id != self.account.account_id
            or self.media_plan.account_id != self.account.account_id
            or self.strategy_brief.feature_packet_sha256 != self.feature_packet_sha256
            or self.media_plan.strategy_brief_sha256 != self.strategy_brief_sha256
            or self.treatment not in self.media_plan.treatments
            or not self.media_plan.publication_allowed
        ):
            raise ValueError("candidate materialization lineage mismatch")
        return self


class StructuredCandidateJudgment(Protocol):
    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class PreparedCandidateJudgment:
    request: CandidateMaterializationRequest
    admission: ExecutionAdmission
    prompt: str
    schema: JsonObject
    receipt: ContextReceipt
    receipt_sha256: str
    workspace: Path

    @property
    def execution_admission(self) -> ExecutionAdmission:
        return self.admission


@dataclass(frozen=True, slots=True)
class HostedCandidateJudgmentExecutor:
    codex: StructuredCandidateJudgment
    output_root: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def prepare(self, task: MarketingTask) -> PreparedCandidateJudgment:
        if task.kind is not TaskKind.MARKETING_JUDGMENT:
            raise MarketingExecutionError("unsupported_candidate_judgment_task")
        try:
            request = CandidateMaterializationRequest.model_validate(task.payload)
        except ValidationError as error:
            raise MarketingExecutionError("candidate_judgment_payload_invalid") from error
        if request.account.account_id != task.account_id:
            raise MarketingExecutionError("candidate_judgment_scope_mismatch")
        schema = _JSON_OBJECT.validate_python(CandidateProposal.model_json_schema())
        prompt = _candidate_prompt(request)
        receipt = ContextReceipt(
            schema_version="trace.context-receipt.v1",
            receipt_id=task.task_id,
            campaign_id=request.campaign_id,
            feature_packet_id=request.feature_packet.packet_id,
            feature_packet_sha256=request.feature_packet_sha256,
            knowledge_snapshot_sha256=request.knowledge_snapshot_sha256,
            capability_snapshot_sha256=_json_sha256({"capabilities": []}),
            prompt_version=_PROMPT_VERSION,
            prompt_sha256=sha256(prompt.encode()).hexdigest(),
            output_schema_version=_SCHEMA_VERSION,
            output_schema_sha256=_json_sha256(schema),
            included_record_ids=(
                request.strategy_brief.brief_id,
                request.media_plan.plan_id,
                request.treatment.treatment_id,
                *request.treatment.claim_ids,
            ),
            omitted_modules=("external_references", "unapproved_learning"),
            created_at=task.created_at,
        )
        request_digest = sha256(task.model_dump_json().encode()).hexdigest()
        root = self.output_root.resolve()
        workspace = (root / _WORKSPACE_DIRECTORY / request_digest).resolve()
        if not workspace.is_relative_to(root):
            raise MarketingExecutionError("candidate_judgment_workspace_invalid")
        try:
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            workspace.chmod(0o700)
        except OSError as error:
            raise MarketingExecutionError("candidate_judgment_workspace_unavailable") from error
        admission = ExecutionAdmission(
            job_digest=request_digest,
            export_nonce=secrets.token_hex(32),
            workspace_id=f"codex-candidate-judgment:{request_digest}",
        )
        return PreparedCandidateJudgment(
            request=request,
            admission=admission,
            prompt=prompt,
            schema=schema,
            receipt=receipt,
            receipt_sha256=contract_sha256(receipt),
            workspace=workspace,
        )

    def execute(self, prepared: PreparedCandidateJudgment) -> TaskResult:
        try:
            raw = self.codex.run_marketing_judgment_job(
                prepared.prompt,
                prepared.schema,
                workspace=prepared.workspace,
                timeout_seconds=self.timeout_seconds,
            )
            proposal = CandidateProposal.model_validate(raw)
        except (CodexCliError, ValidationError) as error:
            raise MarketingExecutionError(
                "candidate_judgment_result_invalid",
                unknown_side_effect=True,
            ) from error
        request = prepared.request
        if (
            set(proposal.claim_ids) != set(request.treatment.claim_ids)
            or proposal.country != request.account.country
            or proposal.image_inputs.language != request.account.language
        ):
            raise MarketingExecutionError(
                "candidate_judgment_claim_or_locale_escape",
                unknown_side_effect=True,
            )
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "pipeline": PIPELINE,
                "judgment": JUDGMENT,
                "campaign_id": request.campaign_id,
                "assignment_id": request.assignment_id,
                "eligible_block_id": request.eligible_block_id,
                "treatment_id": request.treatment.treatment_id,
                "context_receipt": _JSON_OBJECT.validate_python(
                    prepared.receipt.model_dump(mode="json")
                ),
                "context_receipt_sha256": prepared.receipt_sha256,
                "candidate": _JSON_OBJECT.validate_python(proposal.model_dump(mode="json")),
                "candidate_sha256": _json_sha256(proposal.model_dump(mode="json")),
                "tool_actions_created": 0,
            },
        )


def _candidate_prompt(request: CandidateMaterializationRequest) -> str:
    payload = json.dumps(
        {
            "feature_packet": request.feature_packet.model_dump(mode="json"),
            "strategy_brief": request.strategy_brief.model_dump(mode="json"),
            "media_plan": request.media_plan.model_dump(mode="json"),
            "selected_treatment": request.treatment.model_dump(mode="json"),
            "account": request.account.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "당신은 Trace Threads 마케팅 에이전트의 candidate materializer다. 승인된 전략과 "
        "treatment를 실제 검수 가능한 게시물 후보 하나로 만든다. 이 실행은 no-tool이며 "
        "촬영·디자인·게시를 실행하지 않는다. 외부 reference를 찾거나 모방하지 않는다.\n\n"
        "규칙:\n"
        "1. selected_treatment의 claim_ids를 정확히 그대로 반환하고 "
        "그 밖의 제품 주장을 쓰지 않는다.\n"
        "2. hook, caption_direction, proof_narrative를 자연스러운 Threads caption에 구현한다.\n"
        "3. 앱에서 실제로 캡처할 5~8개 시간표 항목과 배경 의도를 image_inputs에 쓴다.\n"
        "4. caption은 인과 효과나 확인되지 않은 출시·성능을 주장하지 않는다.\n"
        "5. 사람의 caption/image 승인 전제이며 자동 게시를 지시하지 않는다.\n\n"
        f"frozen input: {payload}\n"
    )


def _json_sha256(value: JsonObject) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(encoded).hexdigest()

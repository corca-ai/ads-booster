"""Proof-first creative planning as one no-tool official Codex judgment."""

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

    from ads_booster.contracts.marketing_agent import MarketingHypothesis

PIPELINE: Final = "hosted_marketing_judgment_v1"
JUDGMENT: Final = "creative_plan"
_PROMPT_VERSION: Final = "trace.proof-first-creative-planner.v1"
_PROPOSAL_SCHEMA_VERSION: Final = "trace.creative-plan-proposal.v1"
_WORKSPACE_DIRECTORY: Final = "codex-creative-judgment"
_DEFAULT_TIMEOUT_SECONDS: Final = 240.0
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_CAPABILITY_PROOF_KIND: Final = {
    "capture.native_png": "installed_native_capture",
    "copy.text": "copy_only",
}


class CreativeJudgmentModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CreativeAccountSnapshot(CreativeJudgmentModel):
    account_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")]
    timezone: Annotated[str, Field(min_length=1, max_length=100)]


class CreativeCapabilityBinding(CreativeJudgmentModel):
    capability_id: Annotated[
        str,
        Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    descriptor_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    effect_class: Literal["local_artifact"]
    request_schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt_schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner_id: Annotated[
        str,
        Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ]
    binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_binding_digest(self) -> CreativeCapabilityBinding:
        bound_value = self.model_dump(mode="json", exclude={"binding_sha256"})
        if _json_sha256(bound_value) != self.binding_sha256:
            raise ValueError("capability binding digest does not match its descriptor")
        return self


class CreativePlanningRequest(CreativeJudgmentModel):
    pipeline: Literal["hosted_marketing_judgment_v1"]
    judgment: Literal["creative_plan"]
    campaign_id: Annotated[str, Field(min_length=1, max_length=128)]
    feature_packet: FeatureEvidencePacket
    feature_packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_brief: StrategyBrief
    strategy_brief_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    account: CreativeAccountSnapshot
    canonical_principles: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    knowledge_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    available_capabilities: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    capability_bindings: Annotated[
        tuple[CreativeCapabilityBinding, ...],
        Field(min_length=1, max_length=32),
    ]
    capability_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    requested_by: Literal["hosted_workspace"]

    @model_validator(mode="after")
    def validate_bindings(self) -> CreativePlanningRequest:
        if contract_sha256(self.feature_packet) != self.feature_packet_sha256:
            raise ValueError("feature packet digest does not match its frozen payload")
        if contract_sha256(self.strategy_brief) != self.strategy_brief_sha256:
            raise ValueError("strategy brief digest does not match its frozen payload")
        if self.strategy_brief.campaign_id != self.campaign_id:
            raise ValueError("strategy brief campaign does not match creative request")
        if self.strategy_brief.account_id != self.account.account_id:
            raise ValueError("strategy brief account does not match creative request")
        if self.strategy_brief.feature_packet_sha256 != self.feature_packet_sha256:
            raise ValueError("strategy brief feature packet does not match creative request")
        if _json_sha256({"principles": list(self.canonical_principles)}) != (
            self.knowledge_snapshot_sha256
        ):
            raise ValueError("knowledge snapshot digest does not match its principles")
        binding_ids = tuple(binding.capability_id for binding in self.capability_bindings)
        if len(set(binding_ids)) != len(binding_ids) or tuple(sorted(binding_ids)) != binding_ids:
            raise ValueError("capability bindings must use unique sorted IDs")
        if self.available_capabilities != binding_ids:
            raise ValueError("available capabilities do not match bound capabilities")
        binding_snapshot = _JSON_OBJECT.validate_python(
            {
                "capability_bindings": [
                    binding.model_dump(mode="json") for binding in self.capability_bindings
                ]
            }
        )
        if _json_sha256(binding_snapshot) != self.capability_snapshot_sha256:
            raise ValueError("capability snapshot digest does not match its bound values")
        return self

    @property
    def available_capability_ids(self) -> tuple[str, ...]:
        return tuple(binding.capability_id for binding in self.capability_bindings)


class CreativePlanProposal(CreativeJudgmentModel):
    schema_version: Literal["trace.creative-plan-proposal.v1"]
    treatments: Annotated[tuple[CreativeTreatment, ...], Field(min_length=2, max_length=8)]


class StructuredCreativeJudgment(Protocol):
    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class PreparedCreativeJudgment:
    request: CreativePlanningRequest
    execution_admission: ExecutionAdmission
    prompt: str
    schema: JsonObject
    context_receipt: ContextReceipt
    context_receipt_sha256: str
    workspace: Path


@dataclass(frozen=True, slots=True)
class HostedCreativeJudgmentExecutor:
    codex: StructuredCreativeJudgment
    output_root: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def prepare(self, task: MarketingTask) -> PreparedCreativeJudgment:
        if task.kind is not TaskKind.MARKETING_JUDGMENT:
            raise MarketingExecutionError("unsupported_creative_judgment_task")
        try:
            request = CreativePlanningRequest.model_validate(task.payload)
        except ValidationError as error:
            raise MarketingExecutionError("creative_judgment_payload_invalid") from error
        if request.account.account_id != task.account_id:
            raise MarketingExecutionError("creative_judgment_scope_mismatch")
        schema = _JSON_OBJECT.validate_python(CreativePlanProposal.model_json_schema())
        prompt = _creative_prompt(request)
        receipt = ContextReceipt(
            schema_version="trace.context-receipt.v1",
            receipt_id=task.task_id,
            campaign_id=request.campaign_id,
            feature_packet_id=request.feature_packet.packet_id,
            feature_packet_sha256=request.feature_packet_sha256,
            knowledge_snapshot_sha256=request.knowledge_snapshot_sha256,
            capability_snapshot_sha256=request.capability_snapshot_sha256,
            prompt_version=_PROMPT_VERSION,
            prompt_sha256=sha256(prompt.encode()).hexdigest(),
            output_schema_version=_PROPOSAL_SCHEMA_VERSION,
            output_schema_sha256=_json_sha256(schema),
            included_record_ids=(request.strategy_brief.brief_id,),
            omitted_modules=("external_references", "owned_experiment_learning"),
            created_at=task.created_at,
        )
        request_digest = sha256(task.model_dump_json().encode()).hexdigest()
        root = self.output_root.resolve()
        workspace = (root / _WORKSPACE_DIRECTORY / request_digest).resolve()
        if not workspace.is_relative_to(root):
            raise MarketingExecutionError("creative_judgment_workspace_invalid")
        try:
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            workspace.chmod(0o700)
        except OSError as error:
            raise MarketingExecutionError("creative_judgment_workspace_unavailable") from error
        return PreparedCreativeJudgment(
            request=request,
            execution_admission=ExecutionAdmission(
                job_digest=request_digest,
                export_nonce=secrets.token_hex(32),
                workspace_id=f"codex-creative-judgment:{request_digest}",
            ),
            prompt=prompt,
            schema=schema,
            context_receipt=receipt,
            context_receipt_sha256=contract_sha256(receipt),
            workspace=workspace,
        )

    def execute(self, prepared: PreparedCreativeJudgment) -> TaskResult:
        try:
            raw = self.codex.run_marketing_judgment_job(
                prepared.prompt,
                prepared.schema,
                workspace=prepared.workspace,
                timeout_seconds=self.timeout_seconds,
            )
            proposal = CreativePlanProposal.model_validate(raw)
            _validate_treatments(prepared.request, proposal.treatments)
            plan = MediaPlan(
                schema_version="trace.media-plan.v1",
                plan_id=prepared.execution_admission.job_digest,
                campaign_id=prepared.request.campaign_id,
                account_id=prepared.request.account.account_id,
                experiment_id=prepared.request.strategy_brief.experiment.experiment_id,
                strategy_brief_sha256=prepared.request.strategy_brief_sha256,
                context_receipt_sha256=prepared.context_receipt_sha256,
                treatments=proposal.treatments,
                publication_allowed=prepared.request.feature_packet.gate.publication_allowed,
                human_review_required=True,
                created_at=prepared.context_receipt.created_at,
            )
        except (CodexCliError, ValidationError, ValueError) as error:
            raise MarketingExecutionError(
                "creative_judgment_result_invalid",
                unknown_side_effect=True,
            ) from error
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "pipeline": PIPELINE,
                "judgment": JUDGMENT,
                "campaign_id": prepared.request.campaign_id,
                "context_receipt": _JSON_OBJECT.validate_python(
                    prepared.context_receipt.model_dump(mode="json")
                ),
                "context_receipt_sha256": prepared.context_receipt_sha256,
                "media_plan": _JSON_OBJECT.validate_python(plan.model_dump(mode="json")),
                "media_plan_sha256": contract_sha256(plan),
                "publication_allowed": plan.publication_allowed,
                "tool_actions_created": 0,
            },
        )


def _validate_treatments(
    request: CreativePlanningRequest,
    treatments: tuple[CreativeTreatment, ...],
) -> None:
    hypotheses = {item.hypothesis_id: item for item in request.strategy_brief.hypotheses}
    activated = set(request.strategy_brief.experiment.activated_hypothesis_ids)
    planned = {item.hypothesis_id for item in treatments}
    if planned != activated:
        raise ValueError("creative plan must cover each activated hypothesis exactly once")
    for treatment in treatments:
        hypothesis = hypotheses[treatment.hypothesis_id]
        _validate_treatment(request, hypothesis, treatment)


def _validate_treatment(
    request: CreativePlanningRequest,
    hypothesis: MarketingHypothesis,
    treatment: CreativeTreatment,
) -> None:
    if not set(treatment.claim_ids).issubset(hypothesis.claim_ids):
        raise ValueError("creative treatment escaped its strategy hypothesis claims")
    available = set(request.available_capability_ids)
    if any(item.capability_id not in available for item in treatment.artifact_requests):
        raise ValueError("creative treatment requested an unavailable capability")
    if any(
        _CAPABILITY_PROOF_KIND.get(item.capability_id) != item.proof_kind.value
        for item in treatment.artifact_requests
    ):
        raise ValueError("creative treatment capability and proof kind do not match")
    capture_requests = [
        item for item in treatment.artifact_requests if item.capability_id == "capture.native_png"
    ]
    if len(capture_requests) != 1:
        raise ValueError(
            "workspace candidate treatments require exactly one native capture request"
        )


def _creative_prompt(request: CreativePlanningRequest) -> str:
    capabilities = json.dumps(
        list(request.available_capability_ids),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    strategy = json.dumps(
        request.strategy_brief.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    packet = json.dumps(
        request.feature_packet.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        "당신은 Trace의 proof-first Threads 크리에이티브 디렉터다. 매체를 먼저 고르지 말고 "
        "각 전략 가설이 바꾸려는 믿음을 무엇으로 증명할지 결정한다. 이 실행은 계획 전용이며 "
        "도구 호출, 후보 생성, 촬영, 디자인 편집, 게시를 하지 않는다.\n\n"
        "규칙:\n"
        "1. 활성 가설마다 정확히 하나의 treatment를 만든다.\n"
        "2. treatment는 가설의 claim_ids 밖으로 나가지 않는다.\n"
        "3. source-only claim을 설치된 동작처럼 표현하지 않는다. 필요한 installed proof는 "
        "artifact request로 명시할 뿐 존재한다고 말하지 않는다.\n"
        "4. native sequence, bound screen recording, explanatory carousel, designed static, "
        "text-only 중 belief change를 가장 잘 증명하는 형식을 고른다.\n"
        "5. artifact request는 제공된 capability ID만 사용한다.\n"
        "6. 이 계획의 모든 treatment는 workspace 이미지 후보로 materialize되므로 "
        "capture.native_png request를 정확히 하나 포함한다. copy.text만으로 끝나는 "
        "treatment를 만들지 않는다.\n"
        "7. control과 challenger 사이에서 사전등록된 manipulated component 외에는 최대한 "
        "동일하게 유지한다.\n"
        "8. 모든 결과는 사람 검수 전제이며 publication_allowed를 임의로 바꾸지 않는다.\n\n"
        f"사용 가능한 capability: {capabilities}\n"
        f"strategy brief: {strategy}\n"
        f"feature packet: {packet}\n"
    )


def _json_sha256(value: JsonObject) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(encoded).hexdigest()

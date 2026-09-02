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
from ads_booster.workspace import CandidateImageInputs

if TYPE_CHECKING:
    from pathlib import Path

PIPELINE: Final = "hosted_marketing_judgment_v1"
JUDGMENT: Final = "candidate_materialization"
_PROMPT_VERSION: Final = "trace.evidence-bound-candidate.v2"
_SCHEMA_VERSION: Final = "trace.candidate-materialization.v2"
_WORKSPACE_DIRECTORY: Final = "codex-candidate-materialization"
_DEFAULT_TIMEOUT_SECONDS: Final = 240.0
_MIN_WEEKLY_ITEMS: Final = 18
_MAX_WEEKLY_ITEMS: Final = 22
_MIN_WEEKLY_TODOS: Final = 8
_MAX_WEEKLY_TODOS: Final = 12
_MIN_TIMED_ITEMS: Final = 3
_MAX_TIMED_ITEMS: Final = 5
_MIN_SPANNING_ITEMS: Final = 3
_MAX_SPANNING_ITEMS: Final = 4
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class CandidateModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CandidateAccount(CandidateModel):
    account_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")]
    timezone: Annotated[str, Field(min_length=1, max_length=100)]


class CandidateAllocation(CandidateModel):
    method: Literal[
        "balanced_complete_blocks",
        "server_randomized_complete_blocks_v1",
    ]
    randomization_seed_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    rank: Annotated[int, Field(ge=0, le=8)]
    posting_slot: Literal["morning", "evening"] | None = None

    @model_validator(mode="after")
    def validate_randomized_slot(self) -> CandidateAllocation:
        randomized = self.method == "server_randomized_complete_blocks_v1"
        if randomized and (
            self.randomization_seed_sha256 is None
            or self.rank not in (1, 2)
            or self.posting_slot not in ("morning", "evening")
        ):
            raise ValueError("randomized allocation requires a bound exposure slot")
        if not randomized and (
            self.randomization_seed_sha256 is not None
            or self.rank != 0
            or self.posting_slot is not None
        ):
            raise ValueError("descriptive allocation cannot claim a randomized exposure slot")
        return self


class CandidateProposal(CandidateModel):
    schema_version: Literal["trace.candidate-materialization.v2"]
    topic: Annotated[str, Field(min_length=1, max_length=200)]
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    caption: Annotated[str, Field(min_length=1, max_length=10_000)]
    hypothesis: Annotated[str, Field(min_length=1, max_length=2_000)]
    posting_slot: Literal["morning", "evening", "manual"]
    appium_prompt: Annotated[str, Field(max_length=10_000)] = ""
    image_inputs: CandidateImageInputs
    claim_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def validate_full_week_inputs(self) -> CandidateProposal:
        items = self.image_inputs.trace_items
        todos = self.image_inputs.trace_todos
        timed = sum(item.time is not None for item in items)
        spanning = sum(item.days > 1 for item in items)
        if not _MIN_WEEKLY_ITEMS <= len(items) <= _MAX_WEEKLY_ITEMS or not (
            _MIN_WEEKLY_TODOS <= len(todos) <= _MAX_WEEKLY_TODOS
        ):
            raise ValueError("candidate must fill one structured week and its todo column")
        if (
            all(item.day == 0 for item in items)
            or any(item.color is None or not item.title.strip() for item in items)
            or any(not todo.strip() for todo in todos)
        ):
            raise ValueError("candidate schedule must use structured days and colors")
        if not _MIN_TIMED_ITEMS <= timed <= _MAX_TIMED_ITEMS or not (
            _MIN_SPANNING_ITEMS <= spanning <= _MAX_SPANNING_ITEMS
        ):
            raise ValueError("candidate schedule does not match the weekly density contract")
        return self


class CandidateMaterializationRequest(CandidateModel):
    pipeline: Literal["hosted_marketing_judgment_v1"]
    judgment: Literal["candidate_materialization"]
    campaign_id: Annotated[str, Field(min_length=1, max_length=128)]
    assignment_id: Annotated[str, Field(min_length=1, max_length=128)]
    eligible_block_id: Annotated[str, Field(min_length=1, max_length=128)]
    allocation: CandidateAllocation | None = None
    feature_packet: FeatureEvidencePacket
    feature_packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    strategy_brief: StrategyBrief
    strategy_brief_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_plan: MediaPlan
    media_plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    treatment: CreativeTreatment
    treatment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    account: CandidateAccount
    canonical_principles: Annotated[tuple[str, ...], Field(min_length=1, max_length=100)]
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
        if _json_sha256({"principles": list(self.canonical_principles)}) != (
            self.knowledge_snapshot_sha256
        ):
            raise ValueError("knowledge snapshot digest does not match its principles")
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
        schema = _candidate_schema()
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
            if (
                prepared.request.allocation is not None
                and prepared.request.allocation.posting_slot is not None
                and proposal.posting_slot != prepared.request.allocation.posting_slot
            ):
                raise ValueError("candidate posting slot escaped its randomized allocation")
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
        candidate = _candidate_wire_value(proposal)
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
                "candidate": candidate,
                "candidate_sha256": _json_sha256(candidate),
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
            "canonical_principles": list(request.canonical_principles),
            "allocation": (
                request.allocation.model_dump(mode="json")
                if request.allocation is not None
                else None
            ),
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
        "3. image_inputs는 현재 Trace 후보 계약과 같은 주간 화면이어야 한다. trace_items는 "
        "day/days/time/color를 모두 가진 객체 18~22개, trace_todos는 날짜 없는 할 일 "
        "8~12개로 만든다. 요일을 분산하고, time은 3~5개에만 두며, days가 2 이상인 "
        "기간 일정은 3~4개로 만든다.\n"
        "4. caption은 인과 효과나 확인되지 않은 출시·성능을 주장하지 않는다.\n"
        "5. 사람의 caption/image 승인 전제이며 자동 게시를 지시하지 않는다.\n\n"
        "6. allocation.posting_slot이 있으면 candidate.posting_slot에 그 값을 정확히 "
        "사용한다. 이 슬롯은 서버가 고정한 실험 노출 조건이다.\n\n"
        f"frozen input: {payload}\n"
    )


def _candidate_schema() -> JsonObject:
    schema = _JSON_OBJECT.validate_python(CandidateProposal.model_json_schema())
    definitions = _JSON_OBJECT.validate_python(schema["$defs"])
    image = _JSON_OBJECT.validate_python(definitions["CandidateImageInputs"])
    image_properties = _JSON_OBJECT.validate_python(image["properties"])
    trace_items = _JSON_OBJECT.validate_python(image_properties["trace_items"])
    trace_items["minItems"] = _MIN_WEEKLY_ITEMS
    trace_items["maxItems"] = _MAX_WEEKLY_ITEMS
    trace_todos = _JSON_OBJECT.validate_python(image_properties["trace_todos"])
    trace_todos["minItems"] = _MIN_WEEKLY_TODOS
    trace_todos["maxItems"] = _MAX_WEEKLY_TODOS
    image_properties["trace_items"] = trace_items
    image_properties["trace_todos"] = trace_todos
    del image_properties["background_intent"]
    image["properties"] = image_properties
    image["required"] = [
        "trace_items",
        "trace_todos",
        "device_time",
        "background_subject",
        "background_mood",
        "language",
        "background_search_query",
    ]
    entry = _JSON_OBJECT.validate_python(definitions["CandidateScheduleEntry"])
    entry["required"] = ["title", "day", "days", "time", "color"]
    definitions["CandidateImageInputs"] = image
    definitions["CandidateScheduleEntry"] = entry
    schema["$defs"] = definitions
    return schema


def _candidate_wire_value(proposal: CandidateProposal) -> JsonObject:
    image_inputs = proposal.image_inputs
    return {
        "schema_version": proposal.schema_version,
        "topic": proposal.topic.strip(),
        "country": proposal.country,
        "caption": proposal.caption.strip(),
        "hypothesis": proposal.hypothesis.strip(),
        "claim_ids": list(proposal.claim_ids),
        "posting_slot": proposal.posting_slot,
        "appium_prompt": proposal.appium_prompt,
        "image_inputs": {
            "trace_items": [
                {
                    "title": item.title.strip(),
                    "day": item.day,
                    "days": item.days,
                    "time": item.time,
                    "color": item.color,
                }
                for item in image_inputs.trace_items
            ],
            "trace_todos": [todo.strip() for todo in image_inputs.trace_todos],
            "device_time": image_inputs.device_time,
            "background_subject": image_inputs.background_subject.value,
            "background_mood": image_inputs.background_mood.strip(),
            "background_search_query": (
                image_inputs.background_search_query.strip()
                if image_inputs.background_search_query is not None
                else None
            ),
            "language": image_inputs.language,
        },
    }


def _json_sha256(value: JsonObject) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(encoded).hexdigest()

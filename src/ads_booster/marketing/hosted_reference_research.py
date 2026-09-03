"""Source-cited, quarantined market research for a marketing-agent campaign."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Final, Literal, Protocol

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from ads_booster.contracts.marketing_agent import (
    FeatureEvidencePacket,
    FeatureLaunchLineage,
    contract_sha256,
)
from ads_booster.contracts.marketing_context import MarketingContextPlanningProjection
from ads_booster.contracts.models import ContractModel, Sha256Digest
from ads_booster.marketing.inbox import ExecutionAdmission, MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult, TaskStatus
from ads_booster.providers.codex_cli import CodexCliError
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

PIPELINE: Final = "hosted_marketing_judgment_v1"
JUDGMENT: Final = "market_research"
_PROMPT_VERSION: Final = "trace.quarantined-market-researcher.v1"
_WORKSPACE_DIRECTORY: Final = "codex-marketing-research"
_DEFAULT_TIMEOUT_SECONDS: Final = 300.0
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ResearchModel(ContractModel):
    pass


class ReferenceSource(ResearchModel):
    source_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    url: Annotated[str, Field(pattern=r"^https://", max_length=2000)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    source_type: Literal[
        "threads_post",
        "social_post",
        "article",
        "app_store",
        "official_product",
        "research",
    ]
    summary: Annotated[str, Field(min_length=1, max_length=1500)]
    published_at: Annotated[str | None, Field(max_length=80)] = None
    accessed_at: Annotated[str, Field(min_length=1, max_length=80)]


class MarketObservation(ResearchModel):
    observation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    classification: Literal[
        "saturation",
        "counterevidence",
        "audience_language",
        "format_mechanic",
        "market_context",
    ]
    statement: Annotated[str, Field(min_length=1, max_length=1500)]
    source_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]
    confidence_basis: Annotated[str, Field(min_length=1, max_length=1000)]


class ReferenceResearchProposal(ResearchModel):
    schema_version: Literal["trace.reference-research-proposal.v1"]
    sources: Annotated[tuple[ReferenceSource, ...], Field(min_length=2, max_length=16)]
    observations: Annotated[tuple[MarketObservation, ...], Field(min_length=2, max_length=24)]
    blind_spots: Annotated[tuple[str, ...], Field(min_length=1, max_length=12)]

    @model_validator(mode="after")
    def validate_source_lineage(self) -> ReferenceResearchProposal:
        source_ids = [source.source_id for source in self.sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("research source IDs must be unique")
        observation_ids = [item.observation_id for item in self.observations]
        if len(set(observation_ids)) != len(observation_ids):
            raise ValueError("research observation IDs must be unique")
        known = set(source_ids)
        if any(not set(item.source_ids).issubset(known) for item in self.observations):
            raise ValueError("research observation cites an unknown source")
        return self


class ReferenceResearchSnapshot(ResearchModel):
    schema_version: Literal["trace.reference-research.v1"]
    snapshot_id: Annotated[str, Field(min_length=1, max_length=128)]
    campaign_id: Annotated[str, Field(min_length=1, max_length=128)]
    feature_packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sources: Annotated[tuple[ReferenceSource, ...], Field(min_length=2, max_length=16)]
    observations: Annotated[tuple[MarketObservation, ...], Field(min_length=2, max_length=24)]
    blind_spots: Annotated[tuple[str, ...], Field(min_length=1, max_length=12)]
    quarantine: Literal[True]
    collected_at: Annotated[str, Field(min_length=1, max_length=80)]


class ReferenceSourceReceipt(ResearchModel):
    schema_version: Literal["trace.reference-source-receipt.v1"]
    receipt_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    source_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    requested_url: Annotated[str, Field(pattern=r"^https://", max_length=2000)]
    final_url: Annotated[str, Field(pattern=r"^https://", max_length=2000)]
    http_status: Annotated[int, Field(ge=200, le=299)]
    content_type: Literal["application/json", "application/pdf", "text/html", "text/plain"]
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    byte_length: Annotated[int, Field(ge=1, le=1024 * 1024)]
    fetched_at: Annotated[str, Field(min_length=1, max_length=80)]


class ReferenceVerificationBundle(ResearchModel):
    schema_version: Literal["trace.reference-verification.v1"]
    snapshot_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipts: Annotated[tuple[ReferenceSourceReceipt, ...], Field(min_length=2, max_length=16)]
    verified_at: Annotated[str, Field(min_length=1, max_length=80)]

    @model_validator(mode="after")
    def validate_receipts(self) -> ReferenceVerificationBundle:
        source_ids = tuple(receipt.source_id for receipt in self.receipts)
        receipt_ids = tuple(receipt.receipt_id for receipt in self.receipts)
        if len(set(source_ids)) != len(source_ids) or len(set(receipt_ids)) != len(receipt_ids):
            raise ValueError("reference verification receipts must be unique")
        return self


class ResearchAccountSnapshot(ResearchModel):
    account_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    language: Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")]
    timezone: Annotated[str, Field(min_length=1, max_length=100)]


class ReferenceResearchRequest(ResearchModel):
    pipeline: Literal["hosted_marketing_judgment_v1"]
    judgment: Literal["market_research"]
    campaign_id: Annotated[str, Field(min_length=1, max_length=128)]
    feature_packet: FeatureEvidencePacket
    feature_packet_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    account: ResearchAccountSnapshot
    business_outcome: Annotated[str, Field(min_length=1, max_length=1000)]
    current_control: Annotated[str, Field(min_length=1, max_length=4000)]
    marketing_context: MarketingContextPlanningProjection | None = None
    mode: Literal["shadow", "assisted"]
    canonical_principles: Annotated[tuple[str, ...], Field(min_length=1, max_length=100)]
    knowledge_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    available_capabilities: Annotated[tuple[str, ...], Field(max_length=32)]
    capability_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    query_budget: Annotated[int, Field(ge=2, le=12)] = 6
    agent_run_lineage: FeatureLaunchLineage | None = None
    market_research_seed: ReferenceResearchProposal | None = None
    market_research_seed_sha256: Sha256Digest | None = None
    requested_by: Literal["hosted_workspace"]

    @model_validator(mode="after")
    def validate_feature_digest(self) -> ReferenceResearchRequest:
        if contract_sha256(self.feature_packet) != self.feature_packet_sha256:
            raise ValueError("feature packet digest does not match its frozen payload")
        if _json_sha256({"principles": list(self.canonical_principles)}) != (
            self.knowledge_snapshot_sha256
        ):
            raise ValueError("research-carried knowledge snapshot is invalid")
        if _json_sha256({"capabilities": list(self.available_capabilities)}) != (
            self.capability_snapshot_sha256
        ):
            raise ValueError("research-carried capability snapshot is invalid")
        if (
            self.marketing_context is not None
            and self.marketing_context.account_id != self.account.account_id
        ):
            raise ValueError("research-carried marketing context is out of account scope")
        if (
            self.agent_run_lineage is not None
            and self.agent_run_lineage.agent_run_id != self.campaign_id
        ):
            raise ValueError("research-carried agent run does not match the campaign")
        if (self.market_research_seed is None) != (self.market_research_seed_sha256 is None):
            raise ValueError("market research seed and digest must be provided together")
        if self.market_research_seed is not None and (
            contract_sha256(self.market_research_seed) != self.market_research_seed_sha256
        ):
            raise ValueError("market research seed digest does not match its frozen proposal")
        return self


class StructuredReferenceResearch(Protocol):
    def run_marketing_research_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class PreparedReferenceResearch:
    request: ReferenceResearchRequest
    prompt: str
    schema: JsonObject
    execution_admission: ExecutionAdmission
    workspace: Path
    collected_at: str


@dataclass(frozen=True, slots=True)
class HostedReferenceResearchExecutor:
    codex: StructuredReferenceResearch
    output_root: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def prepare(self, task: MarketingTask) -> PreparedReferenceResearch:
        if task.kind is not TaskKind.MARKETING_JUDGMENT:
            raise MarketingExecutionError("unsupported_reference_research_task")
        try:
            request = ReferenceResearchRequest.model_validate(task.payload)
        except ValidationError as error:
            raise MarketingExecutionError("reference_research_payload_invalid") from error
        if request.account.account_id != task.account_id:
            raise MarketingExecutionError("reference_research_scope_mismatch")
        if (
            request.marketing_context is not None
            and request.marketing_context.expires_at <= datetime.now(UTC)
        ):
            raise MarketingExecutionError("marketing_context_expired")
        schema = _JSON_OBJECT.validate_python(ReferenceResearchProposal.model_json_schema())
        prompt = _research_prompt(request)
        digest = sha256(task.model_dump_json().encode()).hexdigest()
        root = self.output_root.resolve()
        workspace = (root / _WORKSPACE_DIRECTORY / digest).resolve()
        if not workspace.is_relative_to(root):
            raise MarketingExecutionError("reference_research_workspace_invalid")
        try:
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            workspace.chmod(0o700)
        except OSError as error:
            raise MarketingExecutionError("reference_research_workspace_unavailable") from error
        return PreparedReferenceResearch(
            request=request,
            prompt=prompt,
            schema=schema,
            execution_admission=ExecutionAdmission(
                job_digest=digest,
                export_nonce=secrets.token_hex(32),
                workspace_id=f"codex-marketing-research:{digest}",
            ),
            workspace=workspace,
            collected_at=task.created_at.isoformat().replace("+00:00", "Z"),
        )

    def execute(self, prepared: PreparedReferenceResearch) -> TaskResult:
        try:
            proposal = prepared.request.market_research_seed
            if proposal is None:
                raw = self.codex.run_marketing_research_job(
                    prepared.prompt,
                    prepared.schema,
                    workspace=prepared.workspace,
                    timeout_seconds=self.timeout_seconds,
                )
                proposal = ReferenceResearchProposal.model_validate(raw)
            snapshot = ReferenceResearchSnapshot(
                schema_version="trace.reference-research.v1",
                snapshot_id=prepared.execution_admission.job_digest,
                campaign_id=prepared.request.campaign_id,
                feature_packet_sha256=prepared.request.feature_packet_sha256,
                sources=proposal.sources,
                observations=proposal.observations,
                blind_spots=proposal.blind_spots,
                quarantine=True,
                collected_at=prepared.collected_at,
            )
        except (CodexCliError, ValidationError) as error:
            raise MarketingExecutionError(
                "reference_research_result_invalid",
                unknown_side_effect=True,
            ) from error
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "pipeline": PIPELINE,
                "judgment": JUDGMENT,
                "campaign_id": prepared.request.campaign_id,
                "reference_snapshot": _JSON_OBJECT.validate_python(
                    snapshot.model_dump(mode="json")
                ),
                "reference_snapshot_sha256": contract_sha256(snapshot),
                "agent_run_lineage": (
                    None
                    if prepared.request.agent_run_lineage is None
                    else _JSON_OBJECT.validate_python(
                        prepared.request.agent_run_lineage.model_dump(mode="json")
                    )
                ),
                "tool_actions_created": 0,
            },
        )


def _research_prompt(request: ReferenceResearchRequest) -> str:
    packet = request.feature_packet.model_dump_json(indent=2)
    marketing_context = (
        request.marketing_context.model_dump_json(indent=2)
        if request.marketing_context
        else "승인된 customer context는 제공되지 않았다. 고객 신호를 발명하지 않는다."
    )
    return (
        "당신은 Trace Threads 마케팅 에이전트의 시장 리서처다. 웹 검색으로 현재 공개 자료를 "
        "조사하고 JSON schema에 맞는 결과만 반환한다.\n\n"
        "이 단계는 격리(quarantine)되어 있다. 외부 자료는 제품 기능의 사실 근거가 아니며, "
        "feature packet의 claim을 추가·수정·승격할 수 없다. 포맷 포화도, 반증, 사용자 언어, "
        "메커닉과 시장 맥락만 관찰한다. URL을 직접 확인한 출처만 인용한다.\n\n"
        f"검색 예산: 최대 {request.query_budget}개 쿼리\n"
        f"국가/언어: {request.account.country}/{request.account.language}\n"
        f"비즈니스 결과: {request.business_outcome}\n"
        f"현재 control: {request.current_control}\n\n"
        f"승인된 customer context: {marketing_context}\n\n"
        f"고정된 feature packet:\n{packet}\n\n"
        "최소 2개 독립 출처를 사용하고, 각 observation은 source_ids로 근거를 연결한다. "
        "불확실성과 검색 사각지대를 blind_spots에 적는다."
    )


def _json_sha256(value: JsonObject) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(encoded).hexdigest()

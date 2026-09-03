"""Hosted, no-effect entrypoint for one durable marketing-agent run."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Final, Literal, Protocol, cast

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from ads_booster.contracts.marketing_agent import contract_sha256
from ads_booster.contracts.marketing_capability import (
    FeatureLaunchIntentIdentifier,
    FeatureLaunchIntentOption,
    FeatureLaunchIntentPlannerReceipt,
    FeatureLaunchIntentSnapshot,
    FeatureLaunchNextIntentDecision,
    ResearchCapabilityScope,
    ResearchCapabilitySnapshot,
)
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.dynamic_evidence_research import (
    DynamicEvidenceResearchResult,
    DynamicEvidenceResearchRunner,
    build_local_research_capability_snapshot,
)
from ads_booster.marketing.evidence_research_operator import EvidenceResearchOperatorError
from ads_booster.marketing.feature_launch_run import FeatureLaunchRunRequest
from ads_booster.marketing.inbox import ExecutionAdmission, MarketingExecutionError
from ads_booster.marketing.models import MarketingTask, TaskKind, TaskResult, TaskStatus
from ads_booster.providers.codex_cli import CodexCli, CodexCliError
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from pathlib import Path

PIPELINE: Final = "hosted_marketing_agent_run_v5"
JUDGMENT: Final = "feature_launch_run"
_WORKSPACE_DIRECTORY: Final = "codex-feature-launch-runs"
_NEXT_INTENT_PROMPT_VERSION: Final = "trace.feature-launch-next-intent-planner.v1"
_RESUME_STEP_SEQUENCE: Final = 2
_DEFAULT_TIMEOUT_SECONDS: Final = 300.0
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class HostedFeatureLaunchRunTask(ContractModel):
    pipeline: Literal["hosted_marketing_agent_run_v5"]
    judgment: Literal["feature_launch_run"]
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    phase: Literal["initial", "resume"]
    step_sequence: Annotated[int, Field(ge=1, le=3)]
    parent_step_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    root_request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resumable_scopes: Annotated[tuple[ResearchCapabilityScope, ...], Field(max_length=1)]
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    launch_request: FeatureLaunchRunRequest
    capability_snapshot: ResearchCapabilitySnapshot
    capability_snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: Annotated[str, Field(min_length=1, max_length=240)]
    requested_by: Literal["hosted_workspace"]

    @model_validator(mode="after")
    def validate_run_binding(self) -> HostedFeatureLaunchRunTask:
        if self.launch_request.agent_run_id != self.run_id:
            raise ValueError("hosted run ID must bind the feature launch request")
        customer_scope: ResearchCapabilityScope = "customer_intelligence"
        expected_initial_scopes = (
            (customer_scope,)
            if customer_scope
            in {scope.value for scope in self.launch_request.research.required_scopes}
            else ()
        )
        if self.phase == "initial" and (
            self.step_sequence != 1
            or self.parent_step_sha256 is not None
            or self.root_request_sha256 != self.request_sha256
            or self.resumable_scopes != expected_initial_scopes
        ):
            raise ValueError("initial hosted feature launch lineage is invalid")
        if self.phase == "resume" and (
            self.step_sequence != _RESUME_STEP_SEQUENCE
            or self.parent_step_sha256 is None
            or self.resumable_scopes
        ):
            raise ValueError("resumed hosted feature launch lineage is invalid")
        if contract_sha256(self.launch_request) != self.request_sha256:
            raise ValueError("hosted feature launch request digest mismatch")
        if contract_sha256(self.capability_snapshot) != self.capability_snapshot_sha256:
            raise ValueError("hosted research capability snapshot digest mismatch")
        expected = build_local_research_capability_snapshot(
            self.launch_request.research.required_scopes
        )
        if self.capability_snapshot != expected:
            raise ValueError("hosted research capability snapshot is unsupported")
        return self


class _NextIntentProposal(ContractModel):
    intent_id: Literal["stop", "request_more_evidence", "propose_shadow_strategy"]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    requested_scope: (
        Literal[
            "product_truth",
            "customer_intelligence",
            "market_evidence",
        ]
        | None
    )

    @model_validator(mode="after")
    def require_requested_scope_shape(self) -> _NextIntentProposal:
        if (self.intent_id == "request_more_evidence") != (self.requested_scope is not None):
            raise ValueError("next intent proposal requested scope is inconsistent")
        return self


class StructuredNextIntentJudgment(Protocol):
    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject: ...


@dataclass(frozen=True, slots=True)
class PreparedHostedFeatureLaunchRun:
    task_id: str
    request: HostedFeatureLaunchRunTask
    execution_admission: ExecutionAdmission
    workspace: Path


@dataclass(frozen=True, slots=True)
class HostedFeatureLaunchRunExecutor:
    """Run dynamic research only; the control plane owns campaign creation."""

    codex_executable: Path
    output_root: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def prepare(self, task: MarketingTask) -> PreparedHostedFeatureLaunchRun:
        if task.kind is not TaskKind.MARKETING_JUDGMENT:
            raise MarketingExecutionError("unsupported_feature_launch_run_task")
        if task.credential_ref is not None:
            raise MarketingExecutionError("feature_launch_run_credential_forbidden")
        try:
            request = HostedFeatureLaunchRunTask.model_validate(task.payload)
        except ValidationError as error:
            raise MarketingExecutionError("feature_launch_run_payload_invalid") from error
        if request.launch_request.research.account_id != task.account_id:
            raise MarketingExecutionError("feature_launch_run_scope_mismatch")
        task_digest = sha256(task.model_dump_json().encode()).hexdigest()
        root = self.output_root.resolve()
        workspace = (root / _WORKSPACE_DIRECTORY / task_digest).resolve()
        if not workspace.is_relative_to(root):
            raise MarketingExecutionError("feature_launch_run_workspace_invalid")
        try:
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            workspace.chmod(0o700)
        except OSError as error:
            raise MarketingExecutionError("feature_launch_run_workspace_unavailable") from error
        return PreparedHostedFeatureLaunchRun(
            task_id=task.task_id,
            request=request,
            execution_admission=ExecutionAdmission(
                job_digest=task_digest,
                export_nonce=secrets.token_hex(32),
                workspace_id=f"codex-feature-launch-run:{task_digest}",
            ),
            workspace=workspace,
        )

    def execute(self, prepared: PreparedHostedFeatureLaunchRun) -> TaskResult:
        request = prepared.request
        codex = CodexCli(
            executable=self.codex_executable,
            model=request.model_id,
        )
        try:
            result = DynamicEvidenceResearchRunner(
                codex=codex,
                state_root=prepared.workspace / "runtime",
                model_id=request.model_id,
                timeout_seconds=self.timeout_seconds,
            ).run(
                request.launch_request.research,
                capability_snapshot=request.capability_snapshot,
            )
            if (
                result.session_id != request.launch_request.research.session_id
                or result.input_snapshot_sha256 != contract_sha256(request.launch_request.research)
                or result.capability_snapshot != request.capability_snapshot
                or result.registry_snapshot_sha256 != request.capability_snapshot_sha256
                or result.planner_protocol_sha256
                != request.capability_snapshot.planner_protocol_sha256
                or result.tool_calls > request.launch_request.research.max_tool_calls
                or result.spent_cost_units > request.launch_request.research.max_cost_units
            ):
                raise MarketingExecutionError("feature_launch_run_result_unbound")
            required_scopes = {
                item.value for item in request.launch_request.research.required_scopes
            }
            if (
                len(result.receipt_chain) != len(required_scopes)
                or {item.scope for item in result.receipt_chain} != required_scopes
                or len(result.receipt_chain) != result.tool_calls
                or sum(item.receipt.actual_cost_units for item in result.receipt_chain)
                != result.spent_cost_units
            ):
                raise MarketingExecutionError("feature_launch_research_receipt_chain_incomplete")
        except (
            CodexCliError,
            EvidenceResearchOperatorError,
            OSError,
            UnicodeError,
            ValidationError,
        ) as error:
            # Research is observe-only. A provider or local persistence failure cannot have
            # published, spent, or mutated the hosted control plane.
            raise MarketingExecutionError("feature_launch_research_failed") from error
        research_result_sha256 = contract_sha256(result)
        intent_snapshot = build_feature_launch_intent_snapshot(
            request.run_id,
            result,
            research_result_sha256=research_result_sha256,
            resumable_scopes=request.resumable_scopes,
        )
        intent_snapshot_sha256 = contract_sha256(intent_snapshot)
        try:
            next_intent = select_feature_launch_next_intent(
                codex,
                prepared=prepared,
                result=result,
                intent_snapshot=intent_snapshot,
                timeout_seconds=self.timeout_seconds,
            )
        except (CodexCliError, OSError, UnicodeError, ValidationError, ValueError) as error:
            raise MarketingExecutionError("feature_launch_next_intent_failed") from error
        return TaskResult(
            status=TaskStatus.SUCCEEDED,
            output={
                "pipeline": PIPELINE,
                "judgment": JUDGMENT,
                "task_id": prepared.task_id,
                "run_id": request.run_id,
                "phase": request.phase,
                "step_sequence": request.step_sequence,
                "parent_step_sha256": request.parent_step_sha256,
                "root_request_sha256": request.root_request_sha256,
                "resumable_scopes": list(request.resumable_scopes),
                "account_id": request.launch_request.research.account_id,
                "request_sha256": request.request_sha256,
                "research_input_sha256": contract_sha256(request.launch_request.research),
                "capability_snapshot": _JSON_OBJECT.validate_python(
                    request.capability_snapshot.model_dump(mode="json")
                ),
                "capability_snapshot_sha256": request.capability_snapshot_sha256,
                "research_result": _JSON_OBJECT.validate_python(result.model_dump(mode="json")),
                "research_result_sha256": research_result_sha256,
                "receipt_chain": [
                    _JSON_OBJECT.validate_python(item.model_dump(mode="json"))
                    for item in result.receipt_chain
                ],
                "intent_snapshot": _JSON_OBJECT.validate_python(
                    intent_snapshot.model_dump(mode="json")
                ),
                "intent_snapshot_sha256": intent_snapshot_sha256,
                "next_intent": _JSON_OBJECT.validate_python(next_intent.model_dump(mode="json")),
                "next_intent_sha256": contract_sha256(next_intent),
                "effect_class": "none",
                "tool_actions_created": 0,
            },
        )


def build_feature_launch_intent_snapshot(
    run_id: str,
    result: DynamicEvidenceResearchResult,
    *,
    research_result_sha256: str | None = None,
    resumable_scopes: tuple[ResearchCapabilityScope, ...] = (),
) -> FeatureLaunchIntentSnapshot:
    """Build eligible no-effect intents; request-more may enter the host-owned bounded resume."""
    insufficient_scopes = cast(
        "tuple[ResearchCapabilityScope, ...]",
        tuple(
            finding.scope.value
            for finding in result.findings
            if finding.evidence_status == "insufficient" and finding.scope.value in resumable_scopes
        ),
    )
    intents = [
        _intent_option(
            intent_id="stop",
            eligibility="always",
            precondition="none",
        )
    ]
    if insufficient_scopes:
        intents.append(
            _intent_option(
                intent_id="request_more_evidence",
                eligibility="insufficient_evidence_present",
                precondition="needs_input_terminal_projection",
                requested_scopes=insufficient_scopes,
            )
        )
    if result.continuation is not None:
        intents.append(
            _intent_option(
                intent_id="propose_shadow_strategy",
                eligibility="exact_research_continuation_present",
                precondition="research_continuation_required",
            )
        )
    return FeatureLaunchIntentSnapshot(
        schema_version="trace.feature-launch-intent-snapshot.v1",
        run_id=run_id,
        research_result_sha256=research_result_sha256 or contract_sha256(result),
        intents=tuple(intents),
    )


def _intent_option(
    *,
    intent_id: FeatureLaunchIntentIdentifier,
    eligibility: Literal[
        "always",
        "insufficient_evidence_present",
        "exact_research_continuation_present",
    ],
    precondition: Literal[
        "none",
        "needs_input_terminal_projection",
        "research_continuation_required",
    ],
    requested_scopes: tuple[ResearchCapabilityScope, ...] = (),
) -> FeatureLaunchIntentOption:
    return FeatureLaunchIntentOption.model_validate(
        {
            "intent_id": intent_id,
            "version": "trace.feature-launch-intent.v1",
            "owner_id": "trace-marketing.hosted-feature-launch-run",
            "effect_class": "none",
            "input_schema_sha256": next_intent_input_schema_sha256(),
            "output_schema_sha256": next_intent_output_schema_sha256(),
            "eligibility": eligibility,
            "precondition": precondition,
            "fixed_cost_units": 0,
            "approval_policy": "none",
            "requested_scopes": requested_scopes,
        }
    )


def next_intent_planner_protocol_sha256() -> str:
    return _json_sha256(
        {
            "schema_version": "trace.feature-launch-next-intent-planner-protocol.v1",
            "prompt_version": _NEXT_INTENT_PROMPT_VERSION,
            "policy": "choose-one-host-admitted-no-effect-intent",
        }
    )


def next_intent_input_schema_sha256() -> str:
    return _json_sha256(
        {
            "schema_version": "trace.feature-launch-next-intent-input-schema.v1",
            "required": [
                "run_id",
                "research_result_sha256",
                "research_state",
                "findings",
                "continuation",
                "intent_snapshot",
            ],
        }
    )


def next_intent_output_schema_sha256() -> str:
    return _json_sha256(_JSON_OBJECT.validate_python(_NextIntentProposal.model_json_schema()))


def select_feature_launch_next_intent(
    codex: StructuredNextIntentJudgment,
    *,
    prepared: PreparedHostedFeatureLaunchRun,
    result: DynamicEvidenceResearchResult,
    intent_snapshot: FeatureLaunchIntentSnapshot,
    timeout_seconds: float,
) -> FeatureLaunchNextIntentDecision:
    """Select a bounded terminal projection without creating a follow-up task or tool action."""
    run_id = prepared.request.run_id
    model_id = prepared.request.model_id
    research_result_sha256 = intent_snapshot.research_result_sha256
    intent_snapshot_sha256 = contract_sha256(intent_snapshot)
    workspace = prepared.workspace / "next-intent"
    expected_snapshot = build_feature_launch_intent_snapshot(
        run_id,
        result,
        resumable_scopes=prepared.request.resumable_scopes,
    )
    if intent_snapshot != expected_snapshot:
        message = "next intent snapshot is not the eligible host projection"
        raise ValueError(message)
    context = _JSON_OBJECT.validate_python(
        {
            "schema_version": "trace.feature-launch-next-intent-context.v1",
            "run_id": run_id,
            "research_result_sha256": research_result_sha256,
            "research_state": result.state,
            "findings": [item.model_dump(mode="json") for item in result.findings],
            "continuation": (
                None if result.continuation is None else result.continuation.model_dump(mode="json")
            ),
            "intent_snapshot": intent_snapshot.model_dump(mode="json"),
        }
    )
    schema = _JSON_OBJECT.validate_python(_NextIntentProposal.model_json_schema())
    if _json_sha256(schema) != next_intent_output_schema_sha256():
        raise ValueError("next intent output schema digest mismatch")
    prompt = (
        "Choose exactly one no-effect next intent from the supplied host snapshot. Return only "
        "JSON matching the schema. Never claim that this worker independently proves the Codex "
        "invocation occurred. request_more_evidence must select one offered requested scope; all "
        "other intents require requested_scope null. "
        f"Prompt contract: {_NEXT_INTENT_PROMPT_VERSION}.\n\n"
        f"Context:\n{_canonical_json(context)}"
    )
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    raw = codex.run_marketing_judgment_job(
        prompt,
        schema,
        workspace=workspace,
        timeout_seconds=timeout_seconds,
    )
    proposal = _NextIntentProposal.model_validate(raw)
    option = next(
        (item for item in intent_snapshot.intents if item.intent_id == proposal.intent_id),
        None,
    )
    if option is None:
        raise ValueError("next intent is not present in the host snapshot")
    if proposal.intent_id == "request_more_evidence":
        if proposal.requested_scope not in option.requested_scopes:
            raise ValueError("next intent requested scope is not insufficient")
    elif proposal.requested_scope is not None:
        raise ValueError("next intent requested scope must be null")
    if proposal.intent_id == "propose_shadow_strategy" and result.continuation is None:
        raise ValueError("shadow strategy intent requires an exact continuation")
    return FeatureLaunchNextIntentDecision(
        schema_version="trace.feature-launch-next-intent-decision.v1",
        run_id=run_id,
        research_result_sha256=research_result_sha256,
        intent_snapshot_sha256=intent_snapshot_sha256,
        intent_id=proposal.intent_id,
        reason=proposal.reason,
        requested_scope=proposal.requested_scope,
        planner_receipt=FeatureLaunchIntentPlannerReceipt(
            schema_version="trace.planner-invocation-receipt.v1",
            provider_id="official-codex-cli",
            model_id=model_id,
            prompt_sha256=sha256(prompt.encode()).hexdigest(),
            context_sha256=_json_sha256(context),
            output_schema_sha256=(
                "38cf82491b68ac5d14a64a6c5e83733f5a9df58b0e4b50fbac2efab161a1a8a2"
            ),
            planner_protocol_sha256=(
                "64890efb66606cc77e5facacaf4c7f62ee1cad18f60247548a1eda98f5566826"
            ),
        ),
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_sha256(value: object) -> str:
    return sha256(_canonical_json(value).encode()).hexdigest()

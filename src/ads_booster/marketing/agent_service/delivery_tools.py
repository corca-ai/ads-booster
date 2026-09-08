"""Model-callable local preparation; trusted Run context never comes from tool input."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.contracts.marketing_delivery import DeliveryProposal, ReviewTarget
from ads_booster.contracts.models import ContractModel, Identifier
from ads_booster.marketing.tool_adapters.compatibility import DelegatedToolResult
from ads_booster.marketing.tool_adapters.descriptors import research_descriptor
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor
    from ads_booster.marketing.agent_service.delivery_review import DeliveryReviewStore
    from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class DeliveryPrepareRequest(ContractModel):
    proposal_id: Identifier
    rationale: str = Field(min_length=1, max_length=4000)
    target: ReviewTarget
    expected_revision: int = Field(default=0, ge=0)


class DeliveryPrepareResult(ContractModel):
    proposal_id: Identifier
    run_id: Identifier
    revision: int = Field(ge=1)
    target_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: str
    state: str
    external_execution_enabled: Literal[False] = False
    review_command: str
    note: str


class DeliveryPreparationTool:
    """Inject an authenticated dispatch-context resolver, never a model-selected tenant."""

    def __init__(
        self,
        store: DeliveryReviewStore,
        *,
        repository: SqliteAgentRunRepository,
    ) -> None:
        self.store: DeliveryReviewStore = store
        self.repository: SqliteAgentRunRepository = repository

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        if descriptor.capability_id != "delivery.prepare":
            raise ValueError("delivery_capability_mismatch")
        if invocation.tenant_id is None:
            raise ValueError("delivery_tenant_context_required")
        run = self.repository.get(invocation.tenant_id, invocation.run_id)
        if run is None or run.run_id != invocation.run_id:
            raise ValueError("delivery_run_not_found")
        request = DeliveryPrepareRequest.model_validate(invocation.input)
        scope = CreativeScope(workspace_id=run.tenant_id, product_id="trace")
        proposal = DeliveryProposal(
            proposal_id=request.proposal_id,
            scope=scope,
            run_id=run.run_id,
            rationale=request.rationale,
            target=request.target,
        )
        previous = self.store.get(scope, proposal.proposal_id)
        if previous is not None and previous.proposal.run_id != run.run_id:
            raise ValueError("delivery_run_binding_conflict")
        # Recover a committed local preparation whose runtime receipt was interrupted.
        if (
            previous is not None
            and previous.revision == request.expected_revision + 1
            and previous.proposal == proposal
        ):
            packet = previous
        else:
            packet = self.store.prepare(
                proposal, actor_scope=scope, expected_revision=request.expected_revision
            )
        return DelegatedToolResult(
            disposition="succeeded",
            actual_cost_units=0,
            output={
                "proposal_id": proposal.proposal_id,
                "run_id": run.run_id,
                "revision": packet.revision,
                "target_sha256": proposal.target_sha256,
                "kind": proposal.target.kind,
                "state": packet.state,
                "external_execution_enabled": False,
                "review_command": f"실행안 검토 {proposal.proposal_id}",
                "note": "준비안 저장 완료. 대상 검토가 필요하며 제작·게시·집행하지 않았습니다.",
            },
        )


def delivery_prepare_descriptor(*, now: datetime) -> ToolDescriptor:
    template = research_descriptor(
        installation_id="installed:delivery.prepare", observed_at=now, ready=True
    )
    schema = _JSON_OBJECT.validate_python(DeliveryPrepareRequest.model_json_schema())
    output = _JSON_OBJECT.validate_python(DeliveryPrepareResult.model_json_schema())
    return template.model_copy(
        update={
            "capability_id": "delivery.prepare",
            "owner": "ads_booster.marketing.agent_service.delivery_tools",
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "output_schema": output,
            "output_schema_sha256": contract_sha256(output),
            "cost": template.cost.model_copy(update={"worst_case_units": 0, "unit": "plan"}),
            "credential_boundary": "none",
        }
    )

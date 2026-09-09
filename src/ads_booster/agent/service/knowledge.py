from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Final, Protocol

from pydantic import TypeAdapter

from ads_booster.agent.core.registry import ToolRegistration
from ads_booster.agent.service.knowledge_transfer import TransferContextMaterial
from ads_booster.contracts.agent_run import (
    AgentRecord,
    AgentRecordKind,
    AgentRun,
    CapabilitySnapshot,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.knowledge_context import (
    ContextTransferValidationAccepted,
    ContextTransferValidationRejected,
    ContextTransferValidationRequest,
    EditorialContextBlock,
    EditorialContextRole,
    KnowledgeContextTransfer,
    ValidationRejectionCode,
    knowledge_context_sha256,
)
from ads_booster.contracts.knowledge_context_validation import TrustedKnowledgeContextBinding
from ads_booster.contracts.knowledge_preparation import (
    BrandUnresolvedPreparation,
    PreparedContextRole,
    PreparedContextSlot,
    PreparedKnowledgeContext,
    RequiredContextErrorCode,
    RequiredContextPreparationError,
)
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.contracts.tool_capability import (
    EffectClass,
    ToolApprovalPolicy,
    ToolCost,
    ToolDescriptor,
    ToolExecutionResult,
    ToolIdempotencyPolicy,
    ToolReadiness,
    ToolReconciliationPolicy,
    allows_authenticated_source_approval,
)
from ads_booster.knowledge.contracts import (
    ActorContext,
    GrantCapability,
    ScopeGrant,
    ScopeKind,
    TaskBinding,
    TaskBindingState,
)
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.grant_policy import authorize_read
from ads_booster.knowledge.operation_enums import CorrectionStatus
from ads_booster.knowledge.repository_context import active_task_binding, context_receipt_is_current
from ads_booster.knowledge.skill_contracts import SkillApplyData
from ads_booster.knowledge.tool_contracts import (
    ApplyData,
    CorrectionData,
    KnowledgeToolName,
    MemoryApplyInput,
    ToolCatalogEntry,
    ToolResult,
    ToolResultStatus,
    TrustedInvocationContext,
)
from ads_booster.transport.json_types import JsonObject

_RECORD_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_CURRENT_IDENTITY: TypeAdapter[tuple[str, int] | None] = TypeAdapter(tuple[str, int] | None)

if TYPE_CHECKING:
    from ads_booster.agent.core.ports import ToolAdapter
    from ads_booster.agent.service.knowledge_ingress import CanonicalKnowledgeIngress
    from ads_booster.agent.service.learning_admission import TerminalExperienceAdmission
    from ads_booster.contracts.agent_memory import MemorySelection
    from ads_booster.knowledge.context_selection import KnowledgeContextAssembler
    from ads_booster.knowledge.legacy_memory import LegacyMemoryGuard
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.repository_learning import LearningReviewCoordinator
    from ads_booster.knowledge.tools import ToolHost

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
READ_ONLY_DM_TOOLS = frozenset(
    {
        KnowledgeToolName.KNOWLEDGE_SEARCH.value,
        KnowledgeToolName.KNOWLEDGE_GET.value,
        KnowledgeToolName.MEMORY_GET.value,
        KnowledgeToolName.MEMORY_EXPLAIN.value,
        KnowledgeToolName.SOURCE_READ.value,
        KnowledgeToolName.SKILL_LIST.value,
        KnowledgeToolName.SKILL_GET.value,
    }
)
_UNBOUND_LEARNING_TOOLS = frozenset(
    {
        KnowledgeToolName.MEMORY_CORRECT.value,
        KnowledgeToolName.SKILL_LIST.value,
        KnowledgeToolName.SKILL_GET.value,
        KnowledgeToolName.SKILL_APPLY.value,
    }
)
_DESCRIPTOR_MISMATCH: Final = "knowledge_tool_descriptor_mismatch"
_LEARNING_SOURCE_CONTEXT_MISMATCH: Final = "knowledge_learning_source_context_mismatch"
_RUN_BINDING_MISSING: Final = "knowledge_run_binding_missing"
_RUN_ACTOR_INACTIVE: Final = "knowledge_run_actor_inactive"
_TASK_BINDING_MISSING: Final = "knowledge_task_binding_missing"
_QUESTION_SOURCE_MISSING: Final = "knowledge_question_source_missing"
_RUN_SOURCE_BINDING_MISMATCH: Final = "knowledge_run_source_binding_mismatch"
_TRANSFER_RECEIPT_INVALID: Final = "knowledge_transfer_receipt_invalid"
_TRANSFER_CONSTRAINT_INVALID: Final = "knowledge_transfer_constraint_invalid"
_TRANSFER_CONTEXT_MISSING: Final = "knowledge_transfer_context_missing"
_TRANSFER_SHARING_AUTHORITY_MISMATCH: Final = "knowledge_transfer_sharing_authority_mismatch"
type PreparationResult = (
    PreparedKnowledgeContext | RequiredContextPreparationError | BrandUnresolvedPreparation
)
type TransferValidationResult = (
    ContextTransferValidationAccepted | ContextTransferValidationRejected
)

_TRANSFER_LIFETIME = timedelta(minutes=15)


class TrustedInvocationResolver(Protocol):
    def resolve_invocation(self, run_id: str, invocation_id: str) -> TrustedInvocationContext: ...

    def consume_foreground_result(
        self,
        name: KnowledgeToolName,
        context: TrustedInvocationContext,
        invocation: ToolInvocation,
        result: ToolResult,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class KnowledgeToolAdapter:
    name: KnowledgeToolName
    host: ToolHost
    resolver: TrustedInvocationResolver

    def execute(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
    ) -> ToolExecutionResult:
        if descriptor.capability_id != self.name.value:
            raise ValueError(_DESCRIPTOR_MISMATCH)
        trusted = self.resolver.resolve_invocation(invocation.run_id, invocation.invocation_id)
        result = self.host.execute(self.name.value, invocation.input, trusted)
        self.resolver.consume_foreground_result(self.name, trusted, invocation, result)
        disposition = (
            "no_effect"
            if descriptor.effect_class is EffectClass.OBSERVE
            else ("failed" if result.error_code is not None else "succeeded")
        )
        return ToolExecutionResult(
            schema_version="trace.tool-execution-result.v1",
            disposition=disposition,
            invocation_sha256=contract_sha256(invocation),
            output=_JSON_OBJECT.validate_python(result.model_dump(mode="json")),
            actual_cost_units=1,
            executor_id=f"knowledge.{self.name.value}",
        )


@dataclass(frozen=True, slots=True)
class _KnowledgeDescriptorFactory:
    host: ToolHost
    name: KnowledgeToolName

    def __call__(self, *, now: datetime) -> ToolDescriptor:
        return next(
            item
            for item in knowledge_descriptors(self.host.catalog(), self.host.schemas(), now=now)
            if item.capability_id == self.name.value
        )


@dataclass(frozen=True, slots=True)
class KnowledgeServiceAdapter:
    ingress: CanonicalKnowledgeIngress
    repository: SqliteKnowledgeRepository
    host: ToolHost
    assembler: KnowledgeContextAssembler
    learning: LearningReviewCoordinator | None = None
    terminal: TerminalExperienceAdmission | None = None
    legacy_memory: LegacyMemoryGuard | None = None

    def prepare(  # noqa: PLR0913 - trusted task and current query are independent context inputs.
        self,
        run: AgentRun,
        snapshot: CapabilitySnapshot,
        *,
        now: datetime,
        action_kind: KnowledgeActionKind | None = None,
        brand_id: str | None = None,
        query: str | None = None,
    ) -> PreparationResult:
        binding = self.ingress.binding_for_run(run.run_id)
        if binding is None:
            return _unresolved(run.run_id, action_kind or KnowledgeActionKind.TEAM_CHAT, brand_id)
        actor = binding.actor
        if self.ingress.authority is not None:
            actor = self.ingress.authority.bind_actor(actor)
        current = active_task_binding(self.repository, actor)
        selected_action = action_kind or (
            KnowledgeActionKind.TEAM_CHAT if current is None else current.action_kind
        )
        selected_brand = (
            brand_id if action_kind is not None else (None if current is None else current.brand_id)
        )
        if self.ingress.pending_fence_for_run(run.run_id):
            return RequiredContextPreparationError(
                schema="knowledge.preparation.v1",
                status="required_context_error",
                task_ref=run.run_id,
                action_kind=selected_action,
                brand_ref=selected_brand,
                error_code=RequiredContextErrorCode.CORRECTION_PENDING,
            )
        task = self._task(actor, run.run_id, selected_action, selected_brand, now)
        if self.legacy_memory is not None:
            _ = self.legacy_memory.select(
                actor,
                run_id=run.run_id,
                query=run.goal.objective if query is None else query,
                now=now,
            )
        filtered = self.filter_snapshot(run.run_id, snapshot)
        prepared = self.assembler.prepare(
            actor,
            task,
            query=(run.goal.objective if query is None else query)[:8000],
            tool_catalog=self.host.catalog(),
            capability_snapshot=filtered,
            now=now,
        )
        if not isinstance(prepared, PreparedKnowledgeContext):
            return prepared
        source = self.ingress.source_for_run(run.run_id)
        if source is None:
            return prepared
        if source.binding != binding:
            return _unresolved(run.run_id, selected_action, selected_brand)
        return self._with_foreground_correction_priority(prepared, source.event.message_id)

    @staticmethod
    def _with_foreground_correction_priority(
        prepared: PreparedKnowledgeContext,
        event_id: str,
    ) -> PreparedKnowledgeContext:
        """Bind foreground correction guidance to the one acknowledged current source event."""
        blocks = tuple(
            block.model_copy(
                update={
                    "text": (
                        block.text
                        + " Current authenticated source event is "
                        + event_id
                        + (
                            ". If its request explicitly corrects a current shared CORE rule or "
                            "named skill, prioritize memory_correct or skill_apply before ordinary "
                            "work. Bind that operation to this exact source; host validation still "
                            "controls authority, scope, heads, and effects. For every selected "
                            "approved legacy memory note, include one exact compatible, unrelated, "
                            "or conflict assessment in a global memory or skill mutation."
                        )
                    )
                }
            )
            if block.block_id == "required.authority"
            else block
            for block in prepared.blocks
        )
        return prepared.model_copy(update={"blocks": blocks})

    def filter_snapshot(
        self,
        run_id: str,
        snapshot: CapabilitySnapshot,
    ) -> CapabilitySnapshot:
        binding = self.ingress.binding_for_run(run_id)
        if binding is None:
            return snapshot.model_copy(
                update={
                    "descriptors": tuple(
                        descriptor
                        for descriptor in snapshot.descriptors
                        if descriptor.capability_id not in _UNBOUND_LEARNING_TOOLS
                    )
                }
            )
        if binding.actor.conversation_scope.kind is not ScopeKind.MEMBER:
            return snapshot
        return snapshot.model_copy(
            update={
                "descriptors": tuple(
                    descriptor
                    for descriptor in snapshot.descriptors
                    if not descriptor.capability_id.startswith(("knowledge_", "memory_", "source_"))
                    or descriptor.capability_id in READ_ONLY_DM_TOOLS
                )
            }
        )

    def is_current(self, run_id: str, prepared: PreparedKnowledgeContext) -> bool:
        if self.ingress.pending_fence_for_run(run_id):
            return False
        binding = self.ingress.binding_for_run(run_id)
        if binding is None:
            return False
        try:
            actor = self._current_read_actor(binding.actor, now=datetime.now(UTC))
            return actor is not None and context_receipt_is_current(
                self.repository,
                actor,
                prepared.receipt,
            )
        except KnowledgePolicyError, ValueError:
            return False

    def _current_read_actor(self, actor: ActorContext, *, now: datetime) -> ActorContext | None:
        """Read persisted authority without recreating removed grants during execution."""
        with self.repository.connection() as db:
            _ = db.execute("BEGIN")
            identity = _CURRENT_IDENTITY.validate_python(
                db.execute(
                    """SELECT member.actor_id,workspace.policy_epoch FROM members AS member
                JOIN workspaces AS workspace USING(workspace_id)
                JOIN memberships AS membership USING(workspace_id,member_id)
                JOIN sessions AS session USING(workspace_id,member_id)
                WHERE member.workspace_id=? AND member.member_id=? AND session.session_id=?
                AND workspace.state='active' AND member.state='active'
                AND membership.state='active' AND session.state='active'""",
                    (actor.workspace_id, actor.member_id, actor.session_id),
                ).fetchone()
            )
            if identity != (actor.actor_id, actor.policy_epoch):
                return None
            rows = _RECORD_ROWS.validate_python(
                db.execute(
                    """SELECT grant_json FROM scope_grants
                WHERE workspace_id=? AND member_id=? AND policy_epoch=? ORDER BY grant_id""",
                    (actor.workspace_id, actor.member_id, actor.policy_epoch),
                ).fetchall()
            )
        current = actor.model_copy(
            update={
                "grants": tuple(
                    grant
                    for row in rows
                    for grant in (ScopeGrant.model_validate_json(row[0]),)
                    if grant.grant_id in {bound.grant_id for bound in actor.grants}
                ),
                "authenticated_at": now,
            }
        )
        _ = authorize_read(actor=current, target_scope=actor.conversation_scope, at=now)
        for grant in actor.grants:
            if grant.capability is GrantCapability.READ:
                _ = authorize_read(actor=current, target_scope=grant.scope, at=now)
        return current

    def resolve_invocation(self, run_id: str, invocation_id: str) -> TrustedInvocationContext:
        return self._trusted_invocation_context(run_id, invocation_id, require_task=True)

    def resolve_question_answer(self, run_id: str, invocation_id: str) -> TrustedInvocationContext:
        """Resolve a shared learning-question reply without opening a planning task."""
        return self._trusted_invocation_context(run_id, invocation_id, require_task=False)

    def select_legacy_memory(
        self,
        run: AgentRun,
        *,
        query: str,
        now: datetime,
    ) -> MemorySelection | None:
        """Select legacy context through the current admitted actor."""
        if self.legacy_memory is None:
            return None
        binding = self.ingress.binding_for_run(run.run_id)
        if binding is None:
            return None
        actor = self._current_read_actor(binding.actor, now=now)
        if actor is None:
            return None
        return self.legacy_memory.select(
            actor,
            run_id=run.run_id,
            query=query,
            now=now,
        )

    def consume_foreground_result(
        self,
        name: KnowledgeToolName,
        context: TrustedInvocationContext,
        invocation: ToolInvocation,
        result: ToolResult,
    ) -> None:
        """Fence actual foreground writes so later learning retains only other source evidence."""
        if self.learning is None or result.status not in {
            ToolResultStatus.APPLIED,
            ToolResultStatus.REPLAYED,
        }:
            return
        target_ids, operation_id = self._consumed_targets(name, invocation, result)
        if not target_ids:
            return
        source = self.ingress.source_for_run(context.run_id)
        if source is None:
            return
        if (
            source.binding.binding_id != context.run_binding_id
            or source.binding.actor.actor_id != context.actor.actor_id
            or source.event != context.source_fetch_event
        ):
            raise ValueError(_LEARNING_SOURCE_CONTEXT_MISMATCH)
        for target_id in target_ids:
            _ = self.learning.consume_target(
                source,
                target_id=target_id,
                operation_id=operation_id,
                at=context.invoked_at,
            )

    @staticmethod
    def _consumed_targets(
        name: KnowledgeToolName,
        invocation: ToolInvocation,
        result: ToolResult,
    ) -> tuple[tuple[str, ...], str]:
        match result.data:
            case CorrectionData(
                status=CorrectionStatus.APPLIED,
                correction_id=operation_id,
                target_id=target_id,
            ) if target_id is not None:
                return (target_id,), operation_id
            case CorrectionData(correction_id=operation_id):
                return (), operation_id
            case SkillApplyData(operation_id=operation_id, target_ids=target_ids):
                return target_ids, operation_id
            case ApplyData() if name is KnowledgeToolName.MEMORY_APPLY:
                request = MemoryApplyInput.model_validate(invocation.input)
                return (
                    request.target_ids,
                    result.operation_id,
                )
            case _:
                return (), result.operation_id

    def _trusted_invocation_context(
        self,
        run_id: str,
        invocation_id: str,
        *,
        require_task: bool,
    ) -> TrustedInvocationContext:
        binding = self.ingress.binding_for_run(run_id)
        if binding is None:
            raise ValueError(_RUN_BINDING_MISSING)
        now = datetime.now(UTC)
        actor = self._current_read_actor(binding.actor, now=now)
        if actor is None:
            raise ValueError(_RUN_ACTOR_INACTIVE)
        task = active_task_binding(self.repository, actor)
        if require_task and task is None:
            raise ValueError(_TASK_BINDING_MISSING)
        source = self.ingress.source_for_run(run_id)
        if source is None and not require_task:
            raise ValueError(_QUESTION_SOURCE_MISSING)
        if source is not None and source.binding != binding:
            raise ValueError(_RUN_SOURCE_BINDING_MISMATCH)
        legacy_selection = None
        if self.legacy_memory is not None:
            legacy_selection = self.legacy_memory.latest(actor, run_id=run_id, now=now)
            if legacy_selection is None:
                legacy_selection = self.legacy_memory.select(
                    actor,
                    run_id=run_id,
                    query="" if source is None else source.event.text,
                    now=now,
                )
        return TrustedInvocationContext(
            invocation_id=invocation_id,
            actor=actor,
            run_binding_id=binding.binding_id,
            run_id=run_id,
            task_id=None if task is None else task.task_id,
            brand_id=None if task is None else task.brand_id,
            capability_epoch=actor.policy_epoch,
            source_fetch_event=None if source is None else source.event,
            legacy_memory_selection=legacy_selection,
            invoked_at=now,
        )

    def registrations(self) -> tuple[ToolRegistration, ...]:
        return tuple(
            ToolRegistration(
                capability_id=name.value,
                version="1",
                adapter=KnowledgeToolAdapter(name=name, host=self.host, resolver=self),
                descriptor_factory=_KnowledgeDescriptorFactory(self.host, name),
            )
            for name in KnowledgeToolName
        )

    def adapters(self) -> dict[str, ToolAdapter]:
        return {
            registration.capability_id: registration.adapter
            for registration in self.registrations()
            if registration.adapter is not None
        }

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        return tuple(registration.descriptor(now=now) for registration in self.registrations())

    def transfer_material(self, prepared: PreparedKnowledgeContext) -> TransferContextMaterial:
        if contract_sha256(prepared.receipt) != prepared.receipt_sha256:
            raise ValueError(_TRANSFER_RECEIPT_INVALID)
        selected = {
            item.constraint_id: item.revision_id for item in prepared.receipt.required_constraints
        }
        constraints = tuple(
            block for block in prepared.blocks if block.slot is PreparedContextSlot.CONSTRAINT
        )
        if (
            len(constraints) != len(selected)
            or len(selected) != len(prepared.receipt.required_constraints)
            or any(
                block.role is not PreparedContextRole.SYSTEM
                or block.revision_refs != (selected.get(block.block_id),)
                for block in constraints
            )
            or len({block.block_id for block in constraints}) != len(constraints)
        ):
            raise ValueError(_TRANSFER_CONSTRAINT_INVALID)
        editorial = tuple(
            EditorialContextBlock(
                block_id=block.block_id,
                role=(
                    EditorialContextRole.CONSTRAINT
                    if block.slot is PreparedContextSlot.CONSTRAINT
                    else EditorialContextRole.REFERENCE
                ),
                text=block.text,
                revision_refs=block.revision_refs,
            )
            for block in prepared.blocks
            if block.slot is PreparedContextSlot.CONSTRAINT
            or (
                block.role is PreparedContextRole.EDITORIAL
                and block.slot
                in {
                    PreparedContextSlot.BRAND_VOICE,
                    PreparedContextSlot.TASK_OVERLAY,
                    PreparedContextSlot.MEMORY,
                    PreparedContextSlot.WIKI,
                }
                and block.revision_refs
            )
        )
        return TransferContextMaterial(
            request=prepared.request,
            receipt=prepared.receipt,
            editorial_context=editorial,
            evidence_excerpts=prepared.evidence_excerpts,
        )

    def outbound_transfer(
        self,
        invocation: ToolInvocation,
        account_id: str,
    ) -> tuple[KnowledgeContextTransfer, JsonObject]:
        prepared = self._prepared_context_for_run(invocation.run_id)
        run_binding = self.ingress.binding_for_run(invocation.run_id)
        if prepared is None or run_binding is None:
            raise ValueError(_TRANSFER_CONTEXT_MISSING)
        binding = TrustedKnowledgeContextBinding(
            workspace_id=run_binding.actor.workspace_id,
            account_id=account_id,
            scoped_actor_ref=run_binding.actor.actor_id,
            brand_ref=prepared.request.brand_ref,
            action_kind=prepared.request.action_kind,
            run_ref=invocation.run_id,
            task_ref=prepared.request.task_ref,
            invocation_ref=invocation.invocation_id,
        )
        transfer = self.create_transfer(
            binding=binding,
            material=self.transfer_material(prepared),
            external_sharing_authority_ref=prepared.receipt.policy_version,
        )
        binding_payload: JsonObject = {
            "workspace_id": binding.workspace_id,
            "account_id": binding.account_id,
            "scoped_actor_ref": binding.scoped_actor_ref,
            "brand_ref": binding.brand_ref,
            "action_kind": binding.action_kind.value,
            "run_ref": binding.run_ref,
            "task_ref": binding.task_ref,
            "invocation_ref": binding.invocation_ref,
        }
        return transfer, binding_payload

    def _prepared_context_for_run(self, run_id: str) -> PreparedKnowledgeContext | None:
        with self.ingress.connect() as connection:
            rows = _RECORD_ROWS.validate_python(
                connection.execute(
                    """SELECT record_json FROM agent_records
                WHERE run_id=? AND kind=? ORDER BY rowid DESC""",
                    (run_id, AgentRecordKind.EVIDENCE.value),
                ).fetchall()
            )
        for row in rows:
            record = AgentRecord.model_validate_json(row[0])
            if record.payload_schema_version != "trace.prepared-knowledge-context-record.v1":
                continue
            payload = record.payload.get("prepared_context")
            if isinstance(payload, dict):
                return PreparedKnowledgeContext.model_validate(payload)
        return None

    def create_transfer(
        self,
        *,
        binding: TrustedKnowledgeContextBinding,
        material: TransferContextMaterial,
        external_sharing_authority_ref: str,
    ) -> KnowledgeContextTransfer:
        if external_sharing_authority_ref != material.receipt.policy_version:
            raise ValueError(_TRANSFER_SHARING_AUTHORITY_MISMATCH)
        transfer_id = (
            "transfer."
            + sha256(
                contract_sha256(
                    {
                        "workspace_id": binding.workspace_id,
                        "account_id": binding.account_id,
                        "scoped_actor_ref": binding.scoped_actor_ref,
                        "brand_ref": binding.brand_ref,
                        "action_kind": binding.action_kind.value,
                        "run_ref": binding.run_ref,
                        "task_ref": binding.task_ref,
                        "invocation_ref": binding.invocation_ref,
                        "receipt_sha256": contract_sha256(material.receipt),
                        "sharing_authority": external_sharing_authority_ref,
                    }
                ).encode()
            ).hexdigest()[:40]
        )
        existing = self.repository.context_transfer(transfer_id)
        if existing is not None:
            return existing
        created_at = datetime.now(UTC)
        transfer = KnowledgeContextTransfer(
            schema="trace.knowledge-context.v1",
            transfer_id=transfer_id,
            workspace_id=binding.workspace_id,
            account_id=binding.account_id,
            scoped_actor_ref=binding.scoped_actor_ref,
            brand_ref=binding.brand_ref,
            action_kind=binding.action_kind,
            run_ref=binding.run_ref,
            task_ref=binding.task_ref,
            invocation_ref=binding.invocation_ref,
            request=material.request,
            receipt=material.receipt,
            editorial_context=material.editorial_context,
            evidence_excerpts=material.evidence_excerpts,
            policy_revision=external_sharing_authority_ref,
            created_at=created_at,
            expires_at=created_at + _TRANSFER_LIFETIME,
        )
        _ = self.repository.record_context_transfer(transfer)
        return transfer

    def validate_transfer(
        self,
        request: ContextTransferValidationRequest,
        *,
        authenticated_tenant_id: str,
        authenticated_principal_id: str,
    ) -> TransferValidationResult:
        checked_at = datetime.now(UTC)
        transfer = self.repository.context_transfer(request.transfer_id)
        previous = self.repository.transfer_validation(
            request.transfer_id,
            request.stage.value,
            request.request_id,
        )
        rejection = self._transfer_rejection(
            request,
            transfer,
            authenticated_tenant_id=authenticated_tenant_id,
            authenticated_principal_id=authenticated_principal_id,
            checked_at=checked_at,
        )
        if previous is not None and (
            previous.principal_id != request.principal_id
            or previous.workspace_id != request.workspace_id
            or previous.account_id != request.account_id
            or previous.knowledge_context_sha256 != request.knowledge_context_sha256
        ):
            rejection = ValidationRejectionCode.TRANSFER_BLOCKED
        if rejection is None and isinstance(previous, ContextTransferValidationAccepted):
            if checked_at < previous.valid_until:
                return previous
            rejection = ValidationRejectionCode.EXPIRED
        if rejection is None and transfer is not None:
            dependencies = self.repository.transfer_dependencies(transfer.transfer_id)
            result: TransferValidationResult = ContextTransferValidationAccepted(
                schema="trace.knowledge-context-validation-result.v1",
                status="accepted",
                request_id=request.request_id,
                principal_id=request.principal_id,
                stage=request.stage,
                transfer_id=request.transfer_id,
                workspace_id=request.workspace_id,
                account_id=request.account_id,
                knowledge_context_sha256=request.knowledge_context_sha256,
                dependency_set_sha256=contract_sha256(
                    {"dependencies": [list(dependency) for dependency in dependencies]}
                ),
                checked_at=checked_at,
                valid_until=transfer.expires_at,
            )
        else:
            result = ContextTransferValidationRejected(
                schema="trace.knowledge-context-validation-result.v1",
                status="rejected",
                request_id=request.request_id,
                principal_id=request.principal_id,
                stage=request.stage,
                transfer_id=request.transfer_id,
                workspace_id=request.workspace_id,
                account_id=request.account_id,
                knowledge_context_sha256=request.knowledge_context_sha256,
                rejection_code=rejection or ValidationRejectionCode.TRANSFER_BLOCKED,
                checked_at=checked_at,
            )
        if (
            previous is None
            and transfer is not None
            and self._request_matches_transfer(request, transfer)
        ):
            self.repository.record_transfer_validation(request, result)
        return result

    def record_replica(self, transfer_id: str, system_id: str, replica_id: str) -> None:
        self.repository.record_transfer_replica(transfer_id, system_id, replica_id)

    def _transfer_rejection(
        self,
        request: ContextTransferValidationRequest,
        transfer: KnowledgeContextTransfer | None,
        *,
        authenticated_tenant_id: str,
        authenticated_principal_id: str,
        checked_at: datetime,
    ) -> ValidationRejectionCode | None:
        if transfer is None or not self._request_matches_transfer(request, transfer):
            return ValidationRejectionCode.TRANSFER_BLOCKED
        if (
            authenticated_tenant_id != request.workspace_id
            or authenticated_principal_id != request.principal_id
        ):
            return ValidationRejectionCode.ACCESS_REVOKED
        if checked_at >= transfer.expires_at:
            return ValidationRejectionCode.EXPIRED
        run_binding = self.ingress.binding_for_run(transfer.run_ref)
        if (
            run_binding is None
            or run_binding.actor.workspace_id != transfer.workspace_id
            or run_binding.actor.actor_id != transfer.scoped_actor_ref
        ):
            return ValidationRejectionCode.ACCESS_REVOKED
        if not context_receipt_is_current(self.repository, run_binding.actor, transfer.receipt):
            return ValidationRejectionCode.DEPENDENCY_CHANGED
        return None

    @staticmethod
    def _request_matches_transfer(
        request: ContextTransferValidationRequest,
        transfer: KnowledgeContextTransfer,
    ) -> bool:
        return (
            request.workspace_id == transfer.workspace_id
            and request.account_id == transfer.account_id
            and request.knowledge_context_sha256 == knowledge_context_sha256(transfer)
        )

    def _task(
        self,
        actor: ActorContext,
        run_id: str,
        action_kind: KnowledgeActionKind,
        brand_id: str | None,
        now: datetime,
    ) -> TaskBinding:
        existing = active_task_binding(
            self.repository,
            actor,
            action_kind=action_kind,
            brand_id=brand_id,
            require_brand_match=True,
        )
        if existing is not None:
            return existing
        current = active_task_binding(self.repository, actor)
        if current is not None:
            _ = self.host.close_task(actor, current.task_id, actor.policy_epoch, now)
        task_id = (
            "task."
            + sha256(f"{run_id}:{action_kind.value}:{brand_id or 'general'}".encode()).hexdigest()[
                :40
            ]
        )
        brand = None if brand_id is None else self.repository.brand(actor, brand_id)
        task = TaskBinding(
            task_id=task_id,
            workspace_id=actor.workspace_id,
            actor_ref=actor.actor_id,
            member_id=actor.member_id,
            session_id=actor.session_id,
            action_kind=action_kind,
            brand_id=brand_id,
            brand_catalog_revision=None if brand is None else brand.revision,
            capability_epoch=actor.policy_epoch,
            state=TaskBindingState.ACTIVE,
            opened_at=now,
        )
        return self.host.open_task(actor, task)


def knowledge_descriptors(
    catalog: tuple[ToolCatalogEntry, ...],
    schemas: dict[KnowledgeToolName, JsonObject],
    *,
    now: datetime,
) -> tuple[ToolDescriptor, ...]:
    output_schema = _JSON_OBJECT.validate_python(ToolResult.model_json_schema())
    empty_schema: JsonObject = {"type": "object", "properties": {}, "additionalProperties": False}
    return tuple(
        _descriptor(
            item,
            schemas[item.name],
            output_schema,
            empty_schema,
            now,
        )
        for item in catalog
    )


def _descriptor(
    item: ToolCatalogEntry,
    input_schema: JsonObject,
    output_schema: JsonObject,
    empty_schema: JsonObject,
    now: datetime,
) -> ToolDescriptor:
    observe = item.required_capability is GrantCapability.READ
    receipt_schema = _JSON_OBJECT.validate_python(ToolExecutionResult.model_json_schema())
    source_bound_foreground_write = allows_authenticated_source_approval(
        capability_id=item.name.value,
        owner="knowledge",
        installation_id="configured:knowledge",
    )
    return ToolDescriptor(
        schema_version="trace.tool-descriptor.v1",
        capability_id=item.name.value,
        version="1",
        owner="knowledge",
        installation_id="configured:knowledge",
        input_schema=input_schema,
        input_schema_sha256=contract_sha256(input_schema),
        output_schema=output_schema,
        output_schema_sha256=contract_sha256(output_schema),
        config_schema=empty_schema,
        config_schema_sha256=contract_sha256(empty_schema),
        receipt_schema=receipt_schema,
        receipt_schema_sha256=contract_sha256(receipt_schema),
        credential_boundary="adapter_owner",
        effect_class=EffectClass.OBSERVE if observe else EffectClass.CONTROL_PLANE_WRITE,
        approval_policy=ToolApprovalPolicy(
            mode="none" if observe or source_bound_foreground_write else "required",
            authority="authenticated_source"
            if source_bound_foreground_write
            else "workspace_member",
        ),
        cost=ToolCost(worst_case_units=1, unit="knowledge_operation"),
        readiness=ToolReadiness(ready=True, observed_at=now, max_age_seconds=300),
        idempotency=ToolIdempotencyPolicy(key_scope="run_tool_input"),
        reconciliation=ToolReconciliationPolicy(
            mode="none" if observe else "manual",
            terminal_dispositions=("no_effect", "succeeded", "failed"),
        ),
    )


def _unresolved(
    task_ref: str,
    action_kind: KnowledgeActionKind,
    brand_id: str | None,
) -> RequiredContextPreparationError:
    return RequiredContextPreparationError(
        schema="knowledge.preparation.v1",
        status="required_context_error",
        task_ref=task_ref,
        action_kind=action_kind,
        brand_ref=brand_id,
        error_code=RequiredContextErrorCode.SCOPE_UNRESOLVED,
    )


__all__ = [
    "READ_ONLY_DM_TOOLS",
    "KnowledgeServiceAdapter",
    "KnowledgeToolAdapter",
    "PreparationResult",
    "TrustedInvocationResolver",
    "knowledge_descriptors",
]

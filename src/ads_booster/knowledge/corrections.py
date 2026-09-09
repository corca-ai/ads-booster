from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Never, override

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contract_types import AuthorityClass, EvidenceKind, MemoryKind, ScopeKind
from ads_booster.knowledge.evidence_contracts import AuthorityRef
from ads_booster.knowledge.governance_contracts import TaskOverlay
from ads_booster.knowledge.grant_policy import authorize_write
from ads_booster.knowledge.memory_contracts import MemoryEntry, MemoryRevision
from ads_booster.knowledge.operation_contracts import MemoryOperation
from ads_booster.knowledge.operation_enums import (
    CorrectionScope,
    CorrectionStatus,
    MemoryOperationKind,
)
from ads_booster.knowledge.tool_contracts import (
    CorrectionData,
    KnowledgeQuestionInput,
    MemoryCorrectInput,
    MemoryRevisionPayload,
    PendingProposal,
    ProposalTargetKind,
    TrustedInvocationContext,
)

if TYPE_CHECKING:
    from ads_booster.contracts.agent_memory import MemoryReference
    from ads_booster.knowledge.legacy_memory import LegacyMemoryGuard
    from ads_booster.knowledge.questions import KnowledgeQuestions
    from ads_booster.knowledge.repository_tool_state import CorrectionTarget, RepositoryToolState
    from ads_booster.knowledge.source_contracts import ConversationEvent


@dataclass(slots=True)
class CorrectionError(Exception):
    code: str
    target: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.target}"


@dataclass(frozen=True, slots=True)
class KnowledgeCorrections:
    state: RepositoryToolState
    questions: KnowledgeQuestions
    legacy_memory: LegacyMemoryGuard | None = None

    def correct(
        self,
        request: MemoryCorrectInput,
        context: TrustedInvocationContext,
    ) -> PreparedCorrection:
        event = self.state.canonical_event(context.actor, request.authenticated_event_ref)
        if not self.state.event_is_bound_to_context(context, event):
            _fail("correction_event_not_bound", request.operation_id)
        match request.scope:
            case CorrectionScope.TASK_ONLY:
                return self._task_only(request, context, event.message_id)
            case CorrectionScope.TEAM:
                conflicts = (
                    ()
                    if self.legacy_memory is None
                    else self.legacy_memory.assess(context, request.legacy_memory_assessments)
                )
                return self._team(request, context, event, conflicts)

    def _task_only(
        self,
        request: MemoryCorrectInput,
        context: TrustedInvocationContext,
        event_id: str,
    ) -> PreparedCorrection:
        if request.task_id is None or context.task_id != request.task_id:
            _fail("task_scope_mismatch", request.operation_id)
        binding = self.state.task_binding(context.actor, request.task_id)
        if binding is None:
            _fail("task_scope_mismatch", request.operation_id)
        if (
            binding.state.value != "active"
            or binding.actor_ref != context.actor.actor_id
            or binding.workspace_id != context.actor.workspace_id
            or binding.member_id != context.actor.member_id
            or binding.session_id != context.actor.session_id
            or binding.capability_epoch != context.capability_epoch
            or binding.brand_id != context.brand_id
            or request.brand_id != context.brand_id
        ):
            _fail("task_scope_mismatch", request.operation_id)
        authority = AuthorityRef(
            event_id=event_id,
            authority_class=AuthorityClass.AUTHORIZED_TASK_INSTRUCTION,
            actor_ref=context.actor.actor_id,
            workspace_id=context.actor.workspace_id,
            scope=context.actor.conversation_scope,
            policy_epoch=context.capability_epoch,
        )
        identity = contract_sha256({"operation": request.operation_id, "event": event_id})
        overlay = TaskOverlay(
            overlay_id=f"overlay.{identity[:32]}",
            task_id=request.task_id,
            workspace_id=context.actor.workspace_id,
            actor_ref=context.actor.actor_id,
            capability_epoch=context.capability_epoch,
            brand_id=context.brand_id,
            instruction=request.correction_text,
            authority_ref=authority,
            reason=f"Task-only correction for {', '.join(request.target_ids)}.",
            created_at=context.invoked_at,
        )
        canonical_event = self.state.canonical_event(context.actor, event_id)
        _ = self.state.mark_event_source_use_only(
            context.actor,
            canonical_event,
            request.operation_id,
            context.invoked_at,
        )
        stored, replayed = self.state.put_task_overlay(context.actor, overlay)
        return PreparedCorrection(
            data=CorrectionData(
                correction_id=request.operation_id,
                status=CorrectionStatus.TASK_ONLY,
                overlay=stored,
                replayed=replayed,
            )
        )

    def _team(
        self,
        request: MemoryCorrectInput,
        context: TrustedInvocationContext,
        event: ConversationEvent,
        legacy_conflicts: tuple[MemoryReference, ...],
    ) -> PreparedCorrection:
        targets = tuple(
            self.state.correction_target(context.actor, target_id)
            for target_id in request.target_ids
        )
        owner = targets[0]
        if any(self._owner_key(item) != self._owner_key(owner) for item in targets[1:]):
            _fail("correction_target_ambiguous", request.operation_id)
        _ = authorize_write(actor=context.actor, target_scope=owner.scope, at=context.invoked_at)
        if request.brand_id != owner.brand_id:
            _fail("correction_brand_mismatch", request.operation_id)
        if event_scope_invalid(event, owner):
            _fail("correction_scope_mismatch", request.operation_id)
        if legacy_conflicts:
            return self._pending(
                request,
                context,
                event,
                owner,
                legacy_conflicts=legacy_conflicts,
            )
        prepared = self._core_publication(request, context, event, owner)
        if prepared is not None:
            return prepared
        return self._pending(request, context, event, owner)

    def _pending(
        self,
        request: MemoryCorrectInput,
        context: TrustedInvocationContext,
        event: ConversationEvent,
        owner: CorrectionTarget,
        *,
        legacy_conflicts: tuple[MemoryReference, ...] = (),
    ) -> PreparedCorrection:
        identity = contract_sha256({"operation": request.operation_id, "event": event.message_id})
        question_id = f"question.{identity[:32]}"
        proposal = PendingProposal(
            proposal_id=f"proposal.{request.operation_id}",
            target_kind=owner.target_kind,
            target_id=owner.target_id,
            expected_revision_id=owner.expected_revision_id,
            brand_id=owner.brand_id,
        )
        result = self.questions.ask(
            KnowledgeQuestionInput(
                schema="knowledge.tool.question.v1",
                question_id=question_id,
                problem=request.correction_text,
                evidence_ids=(event.message_id, *request.target_ids),
                checks_tried=("Resolved the canonical owner and current expected head.",),
                recommendation="Review and explicitly approve the guarded owner change.",
                pending_proposal=proposal,
                source_event_id=event.message_id,
                legacy_memory_receipt=(
                    None
                    if not legacy_conflicts or context.legacy_memory_selection is None
                    else context.legacy_memory_selection.receipt
                ),
                legacy_memory_conflicts=legacy_conflicts,
            ),
            context,
        )
        return PreparedCorrection(
            data=CorrectionData(
                correction_id=request.operation_id,
                status=CorrectionStatus.PENDING,
                question_id=result.question.question_id,
            )
        )

    def _core_publication(
        self,
        request: MemoryCorrectInput,
        context: TrustedInvocationContext,
        event: ConversationEvent,
        owner: CorrectionTarget,
    ) -> PreparedCorrection | None:
        replacement = request.replacement_entry
        if (
            replacement is None
            or len(request.target_ids) != 1
            or request.target_ids != (replacement.entry_id,)
            or request.expected_revision_id != owner.expected_revision_id
            or owner.target_kind is not ProposalTargetKind.MEMORY
        ):
            return None
        stored = self.state.repository.read_memory(context.actor, owner.target_id)
        if (
            stored is None
            or stored.document.kind is not MemoryKind.CORE
            or stored.revision.revision_id != owner.expected_revision_id
            or replacement.document_id != stored.document.document_id
            or replacement.document_kind is not MemoryKind.CORE
            or replacement.scope != owner.scope
            or replacement.entry_id not in stored.revision.entry_ids
            or not any(
                source.evidence_kind is EvidenceKind.CONVERSATION_EVENT
                and source.evidence_id == event.message_id
                and source.revision_id == str(event.revision)
                for source in replacement.source_refs
            )
            or (
                replacement.authority_ref is not None
                and (
                    replacement.authority_ref.event_id != event.message_id
                    or replacement.authority_ref.actor_ref != event.speaker_ref
                    or replacement.authority_ref.workspace_id != event.scope.workspace_id
                    or replacement.authority_ref.scope != event.scope
                    or replacement.authority_ref.policy_epoch != context.capability_epoch
                )
            )
        ):
            return None
        identity = contract_sha256(
            {
                "operation": request.operation_id,
                "document": stored.document.document_id,
                "expected": stored.revision.revision_id,
                "replacement": replacement.model_dump(mode="json"),
            }
        )
        entries = tuple(
            replacement if entry.entry_id == replacement.entry_id else entry
            for entry in stored.entries
        )
        body = render_memory_body(entries)
        revision = MemoryRevision(
            document_id=stored.document.document_id,
            revision_id=f"revision.{identity[:32]}",
            previous_revision_id=stored.revision.revision_id,
            body_sha256=sha256(body).hexdigest(),
            entry_ids=stored.revision.entry_ids,
            created_at=context.invoked_at,
        )
        document = stored.document.model_copy(update={"head_revision_id": revision.revision_id})
        operation = MemoryOperation(
            operation_id=request.operation_id,
            kind=MemoryOperationKind.UPDATE,
            document_id=document.document_id,
            entry_id=replacement.entry_id,
            expected_revision_id=stored.revision.revision_id,
            replacement_entry_id=replacement.entry_id,
            reason=request.correction_text,
            evidence_refs=(event.message_id,),
        )
        return PreparedCorrection(
            data=CorrectionData(
                correction_id=request.operation_id,
                status=CorrectionStatus.APPLIED,
                target_id=document.document_id,
            ),
            memory_change=MemoryRevisionPayload(
                operation=operation,
                document=document,
                revision=revision,
                entries=entries,
                constraints=self.state.memory_constraints(
                    context.actor,
                    stored.document.document_id,
                    stored.revision.revision_id,
                ),
                body=body.decode(),
            ),
        )

    @staticmethod
    def _owner_key(target: CorrectionTarget) -> tuple[str, str, str, str | None]:
        return (
            target.target_kind.value,
            target.target_id,
            target.expected_revision_id,
            target.brand_id,
        )


@dataclass(frozen=True, slots=True)
class PreparedCorrection:
    data: CorrectionData
    memory_change: MemoryRevisionPayload | None = None


def event_scope_invalid(event: ConversationEvent, owner: CorrectionTarget) -> bool:
    shared_scope = event.scope.kind is ScopeKind.WORKSPACE and event.scope == owner.scope
    return not shared_scope or not event.text


def render_memory_body(entries: tuple[MemoryEntry, ...]) -> bytes:
    parts = ["# Memory\n"]
    for entry in entries:
        parts.extend((f"\n## {entry.entry_id}\n\n", entry.text, "\n"))
    return "".join(parts).encode()


def _fail(code: str, target: str) -> Never:
    raise CorrectionError(code, target)


__all__ = [
    "CorrectionError",
    "KnowledgeCorrections",
    "PreparedCorrection",
    "render_memory_body",
]

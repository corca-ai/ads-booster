"""Normalize model-facing CORE drafts into strict memory publication payloads."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Never

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contract_types import (
    AuthorityClass,
    DependencyState,
    EvidenceKind,
    InstructionAuthority,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    MemoryStatus,
    Provenance,
    ScopeKind,
    SourceKind,
    UsageRole,
)
from ads_booster.knowledge.corrections import render_memory_body
from ads_booster.knowledge.evidence_contracts import AuthorityRef, EvidenceRef
from ads_booster.knowledge.memory_contracts import MemoryDocument, MemoryEntry, MemoryRevision
from ads_booster.knowledge.operation_contracts import MemoryOperation
from ads_booster.knowledge.operation_enums import MemoryOperationKind
from ads_booster.knowledge.source_contracts import ConversationEvent
from ads_booster.knowledge.tool_contracts import CoreMemoryDraft, MemoryRevisionPayload

if TYPE_CHECKING:
    from ads_booster.knowledge.tool_contracts import TrustedInvocationContext
    from ads_booster.knowledge.tool_dependencies import ToolDependencies


def normalize_core_memory_draft(
    dependencies: ToolDependencies,
    draft: CoreMemoryDraft,
    context: TrustedInvocationContext,
    operation_id: str,
) -> MemoryRevisionPayload:
    """Create one strict CORE publication payload from a bounded semantic draft."""
    if context.actor.conversation_scope.kind is not ScopeKind.WORKSPACE:
        _reject("core_memory_draft_private_forbidden", draft.document_id)
    stored = dependencies.repository.read_memory(context.actor, draft.document_id)
    if stored is None:
        if draft.expected_revision_id != "none":
            _reject("core_memory_draft_head_mismatch", draft.document_id)
        document = MemoryDocument(
            document_id=draft.document_id,
            workspace_id=context.actor.workspace_id,
            kind=MemoryKind.CORE,
            timezone="UTC",
            head_revision_id="placeholder",
        )
        previous_revision_id = None
        previous_entries: tuple[MemoryEntry, ...] = ()
        constraints = ()
        operation_kind = MemoryOperationKind.ADD
    else:
        if (
            stored.document.kind is not MemoryKind.CORE
            or stored.revision.revision_id != draft.expected_revision_id
        ):
            _reject("core_memory_draft_head_mismatch", draft.document_id)
        document = stored.document
        previous_revision_id = stored.revision.revision_id
        previous_entries = stored.entries
        constraints = dependencies.state.memory_constraints(
            context.actor,
            stored.document.document_id,
            stored.revision.revision_id,
        )
        operation_kind = MemoryOperationKind.UPDATE
    source_refs, authority = _trusted_provenance(dependencies, context, draft)
    entry = MemoryEntry(
        entry_id=draft.entry_id,
        document_id=document.document_id,
        document_kind=MemoryKind.CORE,
        text=draft.text,
        kind=draft.kind,
        status=MemoryStatus.ACTIVE,
        dependency_state=DependencyState.CURRENT,
        origin=MemoryOrigin.DIRECT,
        usage_role=UsageRole.REFERENCE,
        scope=context.actor.conversation_scope,
        source_refs=source_refs,
        applicability=draft.applicability,
        admission_reason=draft.reason,
        authority_ref=authority,
    )
    entries = _replace_or_append(previous_entries, entry)
    body = render_memory_body(entries)
    identity = contract_sha256(
        {
            "operation": operation_id,
            "document": document.document_id,
            "expected": draft.expected_revision_id,
            "draft": draft.model_dump(mode="json"),
            "sources": [item.model_dump(mode="json") for item in source_refs],
        }
    )
    revision_id = f"memory-revision.{identity[:32]}"
    revision = MemoryRevision(
        document_id=document.document_id,
        revision_id=revision_id,
        previous_revision_id=previous_revision_id,
        body_sha256=sha256(body).hexdigest(),
        entry_ids=tuple(item.entry_id for item in entries),
        created_at=context.invoked_at,
    )
    operation = MemoryOperation(
        operation_id=operation_id,
        kind=operation_kind,
        document_id=document.document_id,
        entry_id=entry.entry_id,
        expected_revision_id=draft.expected_revision_id,
        replacement_entry_id=(
            entry.entry_id if operation_kind is MemoryOperationKind.UPDATE else None
        ),
        reason=draft.reason,
        evidence_refs=tuple(item.evidence_id for item in source_refs),
    )
    return MemoryRevisionPayload(
        operation=operation,
        document=document.model_copy(update={"head_revision_id": revision_id}),
        revision=revision,
        entries=entries,
        constraints=constraints,
        body=body.decode(),
    )


def _trusted_provenance(
    dependencies: ToolDependencies,
    context: TrustedInvocationContext,
    draft: CoreMemoryDraft,
) -> tuple[tuple[EvidenceRef, ...], AuthorityRef | None]:
    event = context.source_fetch_event
    if event is None:
        event = _event_from_source_capabilities(dependencies, context)
    if event is not None:
        canonical = dependencies.state.canonical_event(context.actor, event.message_id)
        if canonical != event or not dependencies.state.event_is_bound_to_context(
            context, canonical
        ):
            _reject("core_memory_draft_source_stale", draft.document_id)
        source_ref = EvidenceRef(
            evidence_kind=EvidenceKind.CONVERSATION_EVENT,
            evidence_id=canonical.message_id,
            revision_id=str(canonical.revision),
            quote_sha256=sha256(canonical.text.encode()).hexdigest(),
            scope=canonical.scope,
            instruction_authority=InstructionAuthority.AUTHORIZED_USER,
            provenance=Provenance.HUMAN_DIRECT,
        )
        authority = (
            AuthorityRef(
                event_id=canonical.message_id,
                authority_class=AuthorityClass.AUTHORIZED_TASK_INSTRUCTION,
                actor_ref=canonical.speaker_ref,
                workspace_id=canonical.scope.workspace_id,
                scope=canonical.scope,
                policy_epoch=context.capability_epoch,
            )
            if draft.kind is MemoryEntryKind.DECISION
            else None
        )
        return (source_ref,), authority
    if draft.kind is MemoryEntryKind.DECISION:
        _reject("core_memory_draft_decision_source_required", draft.entry_id)
    source_refs = _segment_source_refs(dependencies, context)
    if not source_refs:
        _reject("core_memory_draft_source_required", draft.entry_id)
    return source_refs, None


def _event_from_source_capabilities(
    dependencies: ToolDependencies,
    context: TrustedInvocationContext,
) -> ConversationEvent | None:
    for capability in context.source_capabilities:
        stored = dependencies.repository.read_source(context.actor, capability.source_id)
        if stored is None or stored.source.revision_id != capability.revision_id:
            _reject("core_memory_draft_source_stale", capability.source_id)
        if stored.source.source_kind is SourceKind.MESSAGE:
            return ConversationEvent.model_validate_json(stored.body)
    return None


def _segment_source_refs(
    dependencies: ToolDependencies,
    context: TrustedInvocationContext,
) -> tuple[EvidenceRef, ...]:
    references: list[EvidenceRef] = []
    for capability in context.source_capabilities:
        stored = dependencies.repository.read_source(context.actor, capability.source_id)
        if stored is None or stored.source.revision_id != capability.revision_id:
            _reject("core_memory_draft_source_stale", capability.source_id)
        segments = {item.segment_id: item for item in stored.segments}
        for segment_id in capability.segment_ids:
            segment = segments.get(segment_id)
            if segment is None:
                _reject("core_memory_draft_segment_unavailable", segment_id)
            references.append(
                EvidenceRef(
                    evidence_kind=EvidenceKind.SOURCE_SEGMENT,
                    evidence_id=segment.segment_id,
                    revision_id=segment.revision_id,
                    segment_id=segment.segment_id,
                    quote_sha256=segment.content_sha256,
                    scope=stored.source.scope,
                    instruction_authority=InstructionAuthority.DATA,
                    provenance=Provenance.EXTERNAL,
                )
            )
    return tuple(references)


def _replace_or_append(
    entries: tuple[MemoryEntry, ...], entry: MemoryEntry
) -> tuple[MemoryEntry, ...]:
    if any(item.entry_id == entry.entry_id for item in entries):
        return tuple(entry if item.entry_id == entry.entry_id else item for item in entries)
    return (*entries, entry)


def _reject(code: str, target: str) -> Never:
    raise ChangeValidationError(code, target)


__all__ = ["normalize_core_memory_draft"]

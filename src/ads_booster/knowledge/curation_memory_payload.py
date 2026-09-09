from __future__ import annotations

# ruff: noqa: EM101
from hashlib import sha256
from typing import TYPE_CHECKING, Final, assert_never
from unicodedata import normalize

from pydantic import TypeAdapter

from ads_booster.knowledge.contract_types import (
    DependencyState,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    MemoryStatus,
    ScopeKind,
    UsageRole,
)
from ads_booster.knowledge.curation_contracts import CurationMemoryDestination
from ads_booster.knowledge.governance_contracts import ConstraintBinding
from ads_booster.knowledge.ingestion_build import stable_id
from ads_booster.knowledge.memory_contracts import MemoryDocument, MemoryRevision
from ads_booster.knowledge.operation_contracts import MemoryOperation
from ads_booster.knowledge.operation_enums import MemoryOperationKind
from ads_booster.knowledge.scope_contracts import AccessScope
from ads_booster.knowledge.tool_contracts import (
    MAX_TOOL_CLAIMS,
    MemoryApplyInput,
    MemoryRevisionPayload,
)
from ads_booster.knowledge.tool_support import KnowledgeToolError

if TYPE_CHECKING:
    from ads_booster.knowledge.memory_contracts import MemoryEntry
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.repository_types import StoredMemory
    from ads_booster.knowledge.scope_contracts import ActorContext
    from ads_booster.knowledge.tool_contracts import TrustedInvocationContext

_MAX_BODY: Final = 100_000


def memory_payload(
    repository: SqliteKnowledgeRepository,
    stored: StoredMemory | None,
    entry: MemoryEntry,
    operation_id: str,
    context: TrustedInvocationContext,
) -> MemoryApplyInput:
    """Keep unrelated entries and constraint bindings in the next complete snapshot."""
    old_entries = () if stored is None else stored.entries
    exists = any(item.entry_id == entry.entry_id for item in old_entries)
    entries = tuple(entry if item.entry_id == entry.entry_id else item for item in old_entries)
    if not exists:
        entries += (entry,)
    body = "\n\n".join(item.text for item in entries)
    if len(entries) > MAX_TOOL_CLAIMS or len(body) > _MAX_BODY:
        raise KnowledgeToolError("curation_memory_capacity_exceeded")
    previous_id = None if stored is None else stored.revision.revision_id
    revision_id = stable_id("memory.revision", operation_id, previous_id or "none")
    document = (
        MemoryDocument(
            document_id=entry.document_id,
            workspace_id=context.actor.workspace_id,
            kind=entry.document_kind,
            timezone="UTC",
            head_revision_id=revision_id,
            scope=entry.scope,
        )
        if stored is None
        else stored.document.model_copy(update={"head_revision_id": revision_id})
    )
    constraints: tuple[ConstraintBinding, ...] = ()
    if stored is not None:
        with repository.connection() as connection:
            rows = TypeAdapter(list[tuple[str]]).validate_python(
                connection.execute(
                    """SELECT binding_json FROM constraint_bindings
                WHERE workspace_id=? AND document_id=? AND memory_revision_id=?""",
                    (context.actor.workspace_id, document.document_id, previous_id),
                ).fetchall()
            )
        constraints = tuple(
            ConstraintBinding.model_validate_json(row[0]).model_copy(
                update={"revision_id": revision_id},
            )
            for row in rows
        )
    return MemoryApplyInput(
        schema="knowledge.tool.memory-apply.v1",
        operation_id=operation_id,
        changes=(
            MemoryRevisionPayload(
                operation=MemoryOperation(
                    operation_id=operation_id,
                    kind=MemoryOperationKind.UPDATE if exists else MemoryOperationKind.ADD,
                    document_id=document.document_id,
                    entry_id=entry.entry_id,
                    expected_revision_id=previous_id or "none",
                    reason=entry.admission_reason,
                    evidence_refs=tuple(ref.evidence_id for ref in entry.source_refs),
                ),
                document=document,
                revision=MemoryRevision(
                    document_id=document.document_id,
                    revision_id=revision_id,
                    previous_revision_id=previous_id,
                    body_sha256=sha256(body.encode()).hexdigest(),
                    entry_ids=tuple(item.entry_id for item in entries),
                    created_at=context.invoked_at,
                ),
                entries=entries,
                constraints=constraints,
                body=body,
            ),
        ),
    )


def existing_subject_entry(stored: StoredMemory | None, subject: str) -> MemoryEntry | None:
    """Preserve a canonical subject's identity without taking over protected memory."""
    if stored is None:
        return None
    candidates = tuple(
        entry
        for entry in stored.entries
        if entry.applicability is not None
        and entry.applicability.subject_key is not None
        and " ".join(normalize("NFKC", entry.applicability.subject_key).split()).casefold()
        == subject.casefold()
    )
    if len(candidates) > 1:
        raise KnowledgeToolError("curation_memory_subject_ambiguous")
    if not candidates:
        return None
    entry = candidates[0]
    if (
        entry.origin is not MemoryOrigin.DIRECT
        or entry.usage_role is not UsageRole.REFERENCE
        or entry.kind is not MemoryEntryKind.FACT
        or entry.authority_ref is not None
        or entry.status is not MemoryStatus.ACTIVE
        or entry.dependency_state is not DependencyState.CURRENT
    ):
        raise KnowledgeToolError("curation_memory_subject_protected")
    return entry


def memory_target(
    destination: CurationMemoryDestination,
    actor: ActorContext,
) -> tuple[MemoryKind, AccessScope]:
    """Resolve model-selected audience exclusively from the authenticated channel member."""
    match destination:
        case CurationMemoryDestination.CHANNEL:
            return MemoryKind.CORE, actor.conversation_scope
        case CurationMemoryDestination.USER:
            channel = actor.conversation_scope
            if channel.kind is not ScopeKind.CHANNEL:
                raise KnowledgeToolError("curation_user_memory_requires_channel")
            return MemoryKind.USER, AccessScope(
                kind=ScopeKind.CHANNEL_MEMBER,
                workspace_id=actor.workspace_id,
                channel_id=channel.channel_id,
                member_id=actor.member_id,
            )
    assert_never(destination)

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from ads_booster.knowledge.contract_types import (
    AuthorityClass,
    ConversationEventKind,
    ConversationRole,
    DependencyState,
    EvidenceKind,
    InstructionAuthority,
    MemoryKind,
    MemoryStatus,
    Provenance,
    ScopeKind,
    SourceKind,
    UsageRole,
)
from ads_booster.knowledge.curation_contracts import (
    CurationConversationEvidence,
    CurationUserEvent,
)
from ads_booster.knowledge.evidence_contracts import AuthorityRef, EvidenceRef
from ads_booster.knowledge.ingestion_build import stable_id
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.repository_tool_state import RepositoryToolState
from ads_booster.knowledge.scope_contracts import AccessScope
from ads_booster.knowledge.source_contracts import ConversationEvent

if TYPE_CHECKING:
    from ads_booster.knowledge.memory_contracts import MemoryEntry
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.repository_types import StoredSource
    from ads_booster.knowledge.scope_contracts import ActorContext

_EVENT_ROWS: Final = TypeAdapter(list[tuple[str]])
_MAX_CONTEXT_CHARACTERS: Final = 24_000
_MAX_KNOWN_MEMORY: Final = 16


def authenticated_user_event(
    repository: SqliteKnowledgeRepository, actor: ActorContext, source: StoredSource
) -> CurationUserEvent | None:
    """Bind readable canonical evidence to its author, never the maintenance actor."""
    if source.source.source_kind is not SourceKind.MESSAGE:
        return None
    original = ConversationEvent.model_validate_json(source.body)
    if (
        original.role is not ConversationRole.USER
        or original.quoted_spans
        or original.event_kind is ConversationEventKind.MESSAGE_DELETED
    ):
        return None
    event = RepositoryToolState(repository).read_canonical_event(actor, original.message_id)
    if event != original:
        msg = "curation_event_binding_mismatch"
        raise ValueError(msg)
    return CurationUserEvent(
        evidence_ref=EvidenceRef(
            evidence_kind=EvidenceKind.CONVERSATION_EVENT,
            evidence_id=event.message_id,
            revision_id=str(event.revision),
            quote_sha256=sha256(event.text.encode()).hexdigest(),
            scope=event.scope,
            instruction_authority=InstructionAuthority.AUTHORIZED_USER,
            provenance=Provenance.HUMAN_DIRECT,
        ),
        authority_ref=AuthorityRef(
            event_id=event.message_id,
            authority_class=AuthorityClass.AUTHORIZED_TASK_INSTRUCTION,
            actor_ref=event.speaker_ref,
            workspace_id=event.scope.workspace_id,
            scope=event.scope,
            policy_epoch=actor.policy_epoch,
        ),
    )


def conversation_evidence(
    repository: SqliteKnowledgeRepository, actor: ActorContext, source: StoredSource
) -> tuple[CurationConversationEvidence, ...]:
    """Read bounded canonical user context at the current event's effective time."""
    if authenticated_user_event(repository, actor, source) is None:
        return ()
    current = ConversationEvent.model_validate_json(source.body)
    cutoff = current.edited_at or current.created_at
    with repository.connection() as connection:
        rows = _EVENT_ROWS.validate_python(
            connection.execute(
                """
                SELECT event.event_json FROM conversation_events AS event
                WHERE event.workspace_id=? AND event.conversation_id=? AND event.scope_key=?
                AND event.revision=(
                    SELECT MAX(latest.revision) FROM conversation_events AS latest
                    WHERE latest.workspace_id=event.workspace_id
                    AND latest.conversation_id=event.conversation_id
                    AND latest.message_id=event.message_id
                    AND latest.scope_key=event.scope_key
                )
                AND json_extract(event.event_json,'$.role')='user'
                AND event.event_kind!='message_deleted'
                AND json_array_length(event.event_json,'$.quoted_spans')=0
                AND length(json_extract(event.event_json,'$.text')) BETWEEN 1 AND 20000
                AND julianday(COALESCE(json_extract(event.event_json,'$.edited_at'),
                                      event.created_at)) <= julianday(?)
                ORDER BY (event.message_id=?) DESC,
                    COALESCE(json_extract(event.event_json,'$.edited_at'),event.created_at) DESC,
                    event.message_id DESC
                LIMIT 12
                """,
                (
                    actor.workspace_id,
                    current.conversation_id,
                    scope_key(current.scope),
                    cutoff.isoformat(),
                    current.message_id,
                ),
            ).fetchall()
        )
    selected: list[CurationConversationEvidence] = []
    remaining = _MAX_CONTEXT_CHARACTERS
    for (event_json,) in rows:
        event = ConversationEvent.model_validate_json(event_json)
        if (event.edited_at or event.created_at) > cutoff or len(event.text) > remaining:
            continue
        source_id = stable_id(
            "source",
            event.scope.model_dump_json(),
            f"message:{event.conversation_id}:{event.message_id}",
        )
        candidate = repository.read_source(actor, source_id)
        if candidate is None or ConversationEvent.model_validate_json(candidate.body) != event:
            continue
        evidence = authenticated_user_event(repository, actor, candidate)
        if evidence is None:
            continue
        selected.append(
            CurationConversationEvidence(
                text=event.text,
                occurred_at=event.edited_at or event.created_at,
                evidence=evidence,
            )
        )
        remaining -= len(event.text)
    return tuple(reversed(selected))


def known_memory(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    context: tuple[CurationConversationEvidence, ...],
) -> tuple[MemoryEntry, ...]:
    """Project the member's personal defaults alongside current-channel reference facts."""
    selected: list[MemoryEntry] = []
    for kind in (MemoryKind.USER, MemoryKind.CORE):
        target_scope = actor.conversation_scope
        if kind is MemoryKind.USER:
            if target_scope.kind is not ScopeKind.CHANNEL:
                continue
            target_scope = AccessScope(
                kind=ScopeKind.CHANNEL_MEMBER,
                workspace_id=actor.workspace_id,
                channel_id=target_scope.channel_id,
                member_id=actor.member_id,
            )
        document_id = repository.find_memory_document_id(actor, kind, None, None)
        if document_id is None:
            continue
        stored = repository.read_memory(actor, document_id)
        if stored is None or stored.document.owned_scope != target_scope:
            continue
        selected.extend(select_reference_memory(stored.entries, context, scope=target_scope))
    return _bounded_reference_memory(tuple(selected))


def select_reference_memory(
    entries: tuple[MemoryEntry, ...],
    context: tuple[CurationConversationEvidence, ...],
    *,
    scope: AccessScope | None = None,
) -> tuple[MemoryEntry, ...]:
    """Keep public active facts, preferring explicit subject matches within a bounded projection."""
    now = datetime.now(UTC)
    candidates = tuple(
        entry
        for entry in entries
        if entry.document_kind in (MemoryKind.CORE, MemoryKind.USER)
        and (entry.scope.kind is ScopeKind.WORKSPACE if scope is None else entry.scope == scope)
        and entry.status is MemoryStatus.ACTIVE
        and entry.dependency_state is DependencyState.CURRENT
        and entry.usage_role is UsageRole.REFERENCE
        and (entry.expires_at is None or entry.expires_at > now)
    )
    text = "\n".join(item.text for item in context).casefold()
    matching = tuple(
        entry
        for entry in candidates
        if entry.applicability is not None
        and entry.applicability.subject_key is not None
        and entry.applicability.subject_key.casefold() in text
    )
    selected = (
        candidates
        if any(entry.document_kind is MemoryKind.USER for entry in candidates)
        else (matching or (candidates if len(candidates) <= _MAX_KNOWN_MEMORY else ()))
    )
    return _bounded_reference_memory(selected)


def _bounded_reference_memory(entries: tuple[MemoryEntry, ...]) -> tuple[MemoryEntry, ...]:
    result: list[MemoryEntry] = []
    remaining = 16_000
    for entry in entries:
        size = len(entry.model_dump_json())
        if len(result) == _MAX_KNOWN_MEMORY:
            break
        if size > remaining:
            continue
        result.append(entry)
        remaining -= size
    return tuple(result)

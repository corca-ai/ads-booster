from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, Never

from pydantic import TypeAdapter

from ads_booster.contracts.knowledge_selection import SelectedSkillSourceRevision
from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contract_types import ConversationRole, EvidenceKind, ScopeKind
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.memory_contracts import MemoryEntry
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.skill_authoring import requested_skill_action
from ads_booster.knowledge.source_contracts import ConversationEvent, Source, SourceSegment
from ads_booster.knowledge.wiki_contracts import Claim

if TYPE_CHECKING:
    from ads_booster.knowledge.evidence_contracts import EvidenceRef
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext

_EVENT_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


def _stale(skill_id: str) -> Never:
    code = "skill_source_stale"
    raise ChangeValidationError(code, skill_id)


def require_current_skill_sources(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    skill_id: str,
    source_refs: tuple[EvidenceRef, ...],
) -> None:
    """Require exact readable source pointers and their current canonical revisions."""
    if any(
        reference.scope.kind not in {ScopeKind.WORKSPACE, ScopeKind.CHANNEL}
        or reference.scope.workspace_id != actor.workspace_id
        or (
            reference.scope.kind is ScopeKind.CHANNEL
            and reference.evidence_kind is not EvidenceKind.CONVERSATION_EVENT
        )
        for reference in source_refs
    ):
        code = "skill_source_scope_invalid"
        raise ChangeValidationError(code, skill_id)
    from ads_booster.knowledge.change_evidence import (  # noqa: PLC0415 - repository cycle
        RepositoryEvidenceResolver,
    )

    _ = RepositoryEvidenceResolver(repository).validate_references(actor, source_refs)
    for reference in source_refs:
        resolved = repository.resolve_evidence(actor, reference)
        match resolved:
            case ConversationEvent():
                if not _event_source_is_current(repository, actor, resolved):
                    _stale(skill_id)
            case SourceSegment(source_id=source_id, revision_id=revision_id):
                current = repository.read_source(actor, source_id)
                if current is None or current.source.revision_id != revision_id:
                    _stale(skill_id)
            case Claim() | MemoryEntry():
                continue


def _event_source_is_current(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    original: ConversationEvent,
) -> bool:
    """Compare canonical provenance while authorizing the current actor only as a reader."""
    from ads_booster.knowledge.repository_source import (  # noqa: PLC0415 - repository cycle
        _require_read,
    )

    with repository.connection() as connection:
        _require_read(connection, actor)
        row = _EVENT_ROW.validate_python(
            connection.execute(
                """
                SELECT event_json FROM conversation_events
                WHERE workspace_id=? AND message_id=?
                ORDER BY revision DESC LIMIT 1
                """,
                (actor.workspace_id, original.message_id),
            ).fetchone()
        )
    if row is None:
        return False
    current = ConversationEvent.model_validate_json(row[0])
    return (
        current.revision == original.revision
        and current.message_id == original.message_id
        and current.conversation_id == original.conversation_id
        and current.speaker_ref == original.speaker_ref
        and current.scope == original.scope
    )


def skill_sources_are_current(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    skill_id: str,
    source_refs: tuple[EvidenceRef, ...],
) -> bool:
    """Return whether every persisted source remains readable and current."""
    try:
        workspace_refs = tuple(ref for ref in source_refs if ref.scope.kind is ScopeKind.WORKSPACE)
        require_current_skill_sources(repository, actor, skill_id, workspace_refs)
        if any(
            ref.scope.kind is not ScopeKind.WORKSPACE
            and published_skill_source_revision(repository, actor, ref) is None
            for ref in source_refs
        ):
            return False
    except ChangeValidationError, KnowledgePolicyError:
        return False
    return True


def published_skill_source_revision(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    reference: EvidenceRef,
) -> SelectedSkillSourceRevision | None:
    """Inspect published-skill provenance without granting access to its channel text.

    Only skill readers use this metadata projection. Source reads/search continue to
    authorize the original channel. An edit, deletion or blocked source invalidates it.
    """
    if (
        reference.scope.kind is not ScopeKind.CHANNEL
        or reference.scope.workspace_id != actor.workspace_id
        or reference.evidence_kind is not EvidenceKind.CONVERSATION_EVENT
    ):
        return None
    from ads_booster.knowledge.repository_source import _require_read  # noqa: PLC0415

    with repository.connection() as connection:
        _require_read(connection, actor)
        row = _EVENT_ROW.validate_python(
            connection.execute(
                """SELECT event_json FROM conversation_events
            WHERE workspace_id=? AND message_id=? ORDER BY revision DESC LIMIT 1""",
                (actor.workspace_id, reference.evidence_id),
            ).fetchone()
        )
        if row is None:
            return None
        event = ConversationEvent.model_validate_json(row[0])
        if (
            str(event.revision) != reference.revision_id
            or event.scope != reference.scope
            or event.role is not ConversationRole.USER
            or sha256(event.text.encode()).hexdigest() != reference.quote_sha256
            or requested_skill_action(event.text) is None
        ):
            return None
        source_row = _EVENT_ROW.validate_python(
            connection.execute(
                """SELECT source.source_json FROM sources AS source
            JOIN source_heads AS head USING(workspace_id,source_id)
            WHERE source.workspace_id=? AND source.source_kind='message'
            AND source.source_identity=? AND source.scope_key=?
            AND json_extract(source.source_json,'$.revision_id')=head.revision_id
            AND source.visibility!='blocked'
            AND NOT EXISTS (SELECT 1 FROM tombstones AS tomb
                WHERE tomb.workspace_id=source.workspace_id AND tomb.target_id=source.source_id
                AND tomb.state IN ('blocked','purge_pending','purged'))""",
                (
                    actor.workspace_id,
                    f"message:{event.conversation_id}:{event.message_id}",
                    scope_key(event.scope),
                ),
            ).fetchone()
        )
    if source_row is None:
        return None
    source = Source.model_validate_json(source_row[0])
    if source.scope != reference.scope:
        return None
    return SelectedSkillSourceRevision(
        source_id=source.source_id,
        revision_id=source.revision_id,
        content_sha256=source.sha256,
    )


__all__ = ["require_current_skill_sources", "skill_sources_are_current"]

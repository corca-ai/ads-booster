from __future__ import annotations

from typing import TYPE_CHECKING, Never

from pydantic import TypeAdapter

from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contract_types import ScopeKind
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.memory_contracts import MemoryEntry
from ads_booster.knowledge.source_contracts import ConversationEvent, SourceSegment
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
        reference.scope.kind is not ScopeKind.WORKSPACE
        or reference.scope.workspace_id != actor.workspace_id
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
        require_current_skill_sources(repository, actor, skill_id, source_refs)
    except ChangeValidationError, KnowledgePolicyError:
        return False
    return True


__all__ = ["require_current_skill_sources", "skill_sources_are_current"]

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import (
    AccessScope,
    Claim,
    ConversationEvent,
    EvidenceKind,
    EvidenceRef,
    EvidenceResolutionError,
    MemoryEntry,
    Source,
    SourceSegment,
    WikiPage,
)
from ads_booster.knowledge.grant_policy import authorize_read
from ads_booster.knowledge.repository_conversation_deletion import READABLE_CONVERSATION_EVENT
from ads_booster.knowledge.repository_source import _require_read

if TYPE_CHECKING:
    from ads_booster.knowledge.contracts import ActorContext
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository
    from ads_booster.knowledge.repository_types import StoredMemory

_STRING = TypeAdapter(str)
type ResolvedEvidence = SourceSegment | Claim | MemoryEntry | ConversationEvent


def resolve_evidence(
    repository: KnowledgeRepository,
    actor: ActorContext,
    reference: EvidenceRef,
) -> ResolvedEvidence:
    _ = authorize_read(actor=actor, target_scope=reference.scope, at=datetime.now(UTC))
    with repository.connection() as connection:
        _require_read(connection, actor)
        match reference.evidence_kind:
            case EvidenceKind.SOURCE_SEGMENT:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        """
                    SELECT segment.segment_json,source.source_json FROM segments AS segment
                    JOIN sources AS source USING(workspace_id,source_id)
                    WHERE segment.workspace_id=? AND segment.segment_id=?
                    AND segment.revision_id=? AND source.visibility!='blocked'
                    ORDER BY segment.extraction_version DESC LIMIT 1
                    """,
                        (actor.workspace_id, reference.evidence_id, reference.revision_id),
                    ).fetchone(),
                )
                if row is not None:
                    source = Source.model_validate_json(_STRING.validate_python(row[1]))
                    _ = authorize_read(actor=actor, target_scope=source.scope, at=datetime.now(UTC))
                    return SourceSegment.model_validate_json(_STRING.validate_python(row[0]))
            case EvidenceKind.CLAIM:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        """
                    SELECT version.claim_json,page.page_json FROM claim_versions AS version
                    JOIN claim_locations AS location
                    ON location.workspace_id=version.workspace_id
                    AND location.claim_id=version.claim_id
                    AND location.claim_revision_id=version.revision_id
                    JOIN wiki_pages AS page ON page.workspace_id=location.workspace_id
                    AND page.page_id=location.page_id
                    WHERE version.workspace_id=? AND version.claim_id=? AND version.revision_id=?
                    AND (location.is_current=0 OR NOT EXISTS (
                        SELECT 1 FROM claim_visibility_fences AS fence
                        WHERE fence.workspace_id=version.workspace_id
                        AND fence.dependent_claim_id=version.claim_id
                        AND fence.dependent_revision_id=version.revision_id
                    ))
                    AND NOT EXISTS (
                        SELECT 1 FROM tombstones AS tomb
                        WHERE tomb.workspace_id=version.workspace_id
                        AND tomb.target_id IN (version.claim_id,page.page_id)
                        AND tomb.state IN ('blocked','purge_pending','purged')
                    ) LIMIT 1
                    """,
                        (actor.workspace_id, reference.evidence_id, reference.revision_id),
                    ).fetchone(),
                )
                if row is not None:
                    page = WikiPage.model_validate_json(_STRING.validate_python(row[1]))
                    _ = authorize_read(actor=actor, target_scope=page.scope, at=datetime.now(UTC))
                    return Claim.model_validate_json(_STRING.validate_python(row[0]))
            case EvidenceKind.MEMORY_ENTRY:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        """
                    SELECT entry.entry_json,scope.scope_json FROM memory_entries AS entry
                    JOIN access_scopes AS scope ON scope.scope_key=entry.scope_key
                    WHERE entry.workspace_id=? AND entry.entry_id=?
                    AND entry.memory_revision_id=? AND entry.dependency_state!='restricted'
                    AND NOT EXISTS (
                        SELECT 1 FROM tombstones AS tomb
                        WHERE tomb.workspace_id=entry.workspace_id
                        AND tomb.target_id IN (entry.entry_id,entry.document_id)
                        AND tomb.state IN ('blocked','purge_pending','purged')
                    ) LIMIT 1
                    """,
                        (actor.workspace_id, reference.evidence_id, reference.revision_id),
                    ).fetchone(),
                )
                if row is not None:
                    scope = AccessScope.model_validate_json(_STRING.validate_python(row[1]))
                    _ = authorize_read(actor=actor, target_scope=scope, at=datetime.now(UTC))
                    return MemoryEntry.model_validate_json(_STRING.validate_python(row[0]))
            case EvidenceKind.CONVERSATION_EVENT:
                row = cast(
                    "tuple[object, ...] | None",
                    connection.execute(
                        f"""
                    SELECT event.event_json,scope.scope_json FROM conversation_events AS event
                    JOIN access_scopes AS scope ON scope.scope_key=event.scope_key
                    WHERE event.workspace_id=? AND event.message_id=?
                    AND CAST(event.revision AS TEXT)=? AND {READABLE_CONVERSATION_EVENT} LIMIT 1
                    """,  # noqa: S608 - static SQL predicate; all input values are bound
                        (actor.workspace_id, reference.evidence_id, reference.revision_id),
                    ).fetchone(),
                )
                if row is not None:
                    event = ConversationEvent.model_validate_json(_STRING.validate_python(row[0]))
                    _ = authorize_read(actor=actor, target_scope=event.scope, at=datetime.now(UTC))
                    return event
    raise EvidenceResolutionError(
        evidence_id=reference.evidence_id,
        revision_id=reference.revision_id,
    )


def memory_dependents(
    repository: KnowledgeRepository,
    actor: ActorContext,
    evidence_ids: tuple[str, ...],
) -> tuple[StoredMemory, ...]:
    if not evidence_ids:
        return ()
    evidence_ids_json = json.dumps(evidence_ids)
    with repository.connection() as connection:
        _require_read(connection, actor)
        rows = cast(
            "list[tuple[object, ...]]",
            connection.execute(
                """
                SELECT DISTINCT reference.document_id FROM derived_memory_refs AS reference
                JOIN memory_heads AS head ON head.workspace_id=reference.workspace_id
                AND head.document_id=reference.document_id
                AND head.revision_id=reference.memory_revision_id
                WHERE reference.workspace_id=? AND reference.upstream_kind='wiki_claim'
                    AND reference.upstream_id IN (
                    SELECT value FROM json_each(?)
                )
                ORDER BY reference.document_id
                """,
                (actor.workspace_id, evidence_ids_json),
            ).fetchall(),
        )
    results: list[StoredMemory] = []
    for row in rows:
        stored = repository.read_memory(actor, _STRING.validate_python(row[0]))
        if stored is not None:
            results.append(stored)
    return tuple(results)


__all__ = ["ResolvedEvidence", "memory_dependents", "resolve_evidence"]

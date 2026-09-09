from __future__ import annotations

import os
import sqlite3
import stat
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import (
    AccessScope,
    ConstraintBinding,
    MemoryDocument,
    MemoryEntry,
    MemoryRevision,
    ScopeKind,
)
from ads_booster.knowledge.deletion import (
    DeletionError,
    PurgeManifest,
    PurgeManifestEntry,
    PurgeReceipt,
    PurgeRequest,
    PurgeState,
    ReplicaPurgeReceipt,
    ReplicaPurgeRequest,
    RetractionReceipt,
    RetractionRequest,
)
from ads_booster.knowledge.erase_ledger import EraseLedgerEntry, EraseTarget
from ads_booster.knowledge.file_paths import MemoryRevisionTarget, RevisionFileDraft
from ads_booster.knowledge.grant_policy import authorize_purge, authorize_write
from ads_booster.knowledge.repository_conversation_deletion import (
    scrub_source_conversation_events,
    source_conversation_event_ids,
)
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.repository_memory import assert_memory_head, insert_memory_revision
from ads_booster.knowledge.repository_source import _require_read
from ads_booster.knowledge.repository_types import (
    HeadExpectation,
    MembershipRole,
    MemoryRevisionWrite,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository

_PURGE_ARTIFACT_ROWS: Final = TypeAdapter(
    list[tuple[int, str, str, str, str | None, str | None, str]]
)
_REDACTED_JSON: Final = '{"redacted":true}'


def retract_target(
    repository: KnowledgeRepository,
    request: RetractionRequest,
) -> RetractionReceipt:
    with repository.connection() as connection:
        _require_read(connection, request.actor)
        target_scope = _target_scope(connection, request.actor.workspace_id, request.target)
        _ = authorize_write(actor=request.actor, target_scope=target_scope, at=request.occurred_at)
        _ = connection.execute("BEGIN IMMEDIATE")
        replay = connection.execute(
            "SELECT tombstone_id,created_at FROM tombstones WHERE operation_id=?",
            (request.operation_id,),
        ).fetchone()
        if replay is not None:
            receipt = _retraction_receipt(connection, request, str(replay[0]), str(replay[1]))
        else:
            _insert_operation(
                connection,
                request.operation_id,
                request.actor.workspace_id,
                "retract",
                request.target,
                request.occurred_at,
            )
            tombstone_id = _block_target(
                connection,
                request.actor.workspace_id,
                request.target,
                request.operation_id,
                request.occurred_at,
                "blocked",
            )
            _record_memory_history_redactions(
                connection,
                request.operation_id,
                request.actor.workspace_id,
            )
            receipt = _retraction_receipt(
                connection,
                request,
                tombstone_id,
                request.occurred_at.isoformat(),
            )
    _ = clean_mixed_memory_revisions(repository, request.actor, request.operation_id)
    return receipt


def require_purge_authority(repository: KnowledgeRepository, request: PurgeRequest) -> None:
    with repository.connection() as connection:
        _require_read(connection, request.actor)
        target_scope = _target_scope(connection, request.actor.workspace_id, request.target)
        role = connection.execute(
            """
            SELECT role FROM memberships
            WHERE workspace_id=? AND member_id=? AND state='active'
            """,
            (request.actor.workspace_id, request.actor.member_id),
        ).fetchone()
        if role is None or str(role[0]) != MembershipRole.ADMIN.value:
            raise DeletionError("purge_admin_required", request.target.entity_id)
        _ = authorize_purge(actor=request.actor, target_scope=target_scope, at=request.occurred_at)


def require_reconcile_authority(
    repository: KnowledgeRepository,
    actor: ActorContext,
    request_id: str,
) -> None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        row = connection.execute(
            """
            SELECT request.target_scope_key,scope.scope_json
            FROM deletion_requests AS request
            JOIN access_scopes AS scope ON scope.scope_key=request.target_scope_key
            WHERE request.request_id=? AND request.workspace_id=?
            """,
            (request_id, actor.workspace_id),
        ).fetchone()
        if row is None:
            raise DeletionError("purge_request_not_found", request_id)
        role = connection.execute(
            """
            SELECT role FROM memberships
            WHERE workspace_id=? AND member_id=? AND state='active'
            """,
            (actor.workspace_id, actor.member_id),
        ).fetchone()
        if role is None or str(role[0]) != MembershipRole.ADMIN.value:
            raise DeletionError("purge_admin_required", request_id)
        target_scope = AccessScope.model_validate_json(str(row[1]))
        _ = authorize_purge(actor=actor, target_scope=target_scope, at=datetime.now(UTC))


def prepare_purge_manifest(
    repository: KnowledgeRepository,
    request: PurgeRequest,
) -> PurgeManifest:
    with repository.connection() as connection:
        entries = _manifest_entries(connection, request.actor.workspace_id, request.target)
    return PurgeManifest(
        request_id=request.request_id,
        workspace_id=request.actor.workspace_id,
        target=request.target,
        entries=entries,
    )


def persist_purge_request(
    repository: KnowledgeRepository,
    request: PurgeRequest,
    manifest: PurgeManifest,
    manifest_sha256: str,
    ledger_entry: EraseLedgerEntry,
) -> None:
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        replay = connection.execute(
            "SELECT manifest_sha256 FROM deletion_requests WHERE request_id=?",
            (request.request_id,),
        ).fetchone()
        if replay is not None:
            if str(replay[0]) != manifest_sha256:
                raise DeletionError("purge_request_idempotency_conflict", request.request_id)
            return
        target_scope = _target_scope(connection, request.actor.workspace_id, request.target)
        _insert_operation(
            connection,
            request.request_id,
            request.actor.workspace_id,
            "purge",
            request.target,
            request.occurred_at,
        )
        _ = connection.execute(
            """
            INSERT INTO deletion_requests(
                request_id,workspace_id,actor_ref,target_kind,target_id,target_revision_id,
                target_scope_key,manifest_sha256,ledger_sequence,ledger_entry_sha256,state,
                reason_code,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,'purge_pending',?,?,?)
            """,
            (
                request.request_id,
                request.actor.workspace_id,
                request.actor.actor_id,
                request.target.kind,
                request.target.entity_id,
                request.target.revision_id or "",
                scope_key(target_scope),
                manifest_sha256,
                ledger_entry.sequence,
                ledger_entry.entry_sha256,
                request.reason_code,
                request.occurred_at.isoformat(),
                request.occurred_at.isoformat(),
            ),
        )
        for ordinal, entry in enumerate(manifest.entries):
            _ = connection.execute(
                """
                INSERT INTO deletion_manifest_entries(
                    request_id,ordinal,artifact_kind,entity_id,revision_id,
                    relative_path,content_sha256,state
                ) VALUES (?,?,?,?,?,?,?,'purge_pending')
                """,
                (
                    request.request_id,
                    ordinal,
                    entry.kind,
                    entry.entity_id,
                    entry.revision_id or "",
                    entry.relative_path,
                    entry.content_sha256,
                ),
            )
        _ = _block_target(
            connection,
            request.actor.workspace_id,
            request.target,
            request.request_id,
            request.occurred_at,
            "purge_pending",
        )
        _persist_dependency_blocks(connection, request.request_id, request.actor.workspace_id)
        _mark_replica_purge_pending(connection, request.request_id, request.actor.workspace_id)
        _record_memory_history_redactions(connection, request.request_id, request.actor.workspace_id)


def existing_purge_receipt(
    repository: KnowledgeRepository,
    actor: ActorContext,
    request_id: str,
) -> PurgeReceipt | None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        row = connection.execute(
            "SELECT workspace_id FROM deletion_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        if str(row[0]) != actor.workspace_id:
            raise DeletionError("purge_workspace_mismatch", request_id)
        return _purge_receipt(connection, request_id, (), datetime.now(UTC))


def read_purge_manifest(
    repository: KnowledgeRepository,
    actor: ActorContext,
    request_id: str,
) -> PurgeManifest:
    require_reconcile_authority(repository, actor, request_id)
    with repository.connection() as connection:
        request_row = connection.execute(
            """
            SELECT workspace_id,target_kind,target_id,target_revision_id
            FROM deletion_requests WHERE request_id=?
            """,
            (request_id,),
        ).fetchone()
        if request_row is None:
            raise DeletionError("purge_request_not_found", request_id)
        rows = connection.execute(
            """
            SELECT artifact_kind,entity_id,revision_id,relative_path,content_sha256
            FROM deletion_manifest_entries WHERE request_id=? ORDER BY ordinal
            """,
            (request_id,),
        ).fetchall()
    target_revision = str(request_row[3])
    return PurgeManifest(
        request_id=request_id,
        workspace_id=str(request_row[0]),
        target=EraseTarget(
            kind=str(request_row[1]),
            entity_id=str(request_row[2]),
            revision_id=target_revision or None,
        ),
        entries=tuple(
            PurgeManifestEntry(
                kind=str(row[0]),
                entity_id=str(row[1]),
                revision_id=str(row[2]) or None,
                relative_path=None if row[3] is None else str(row[3]),
                content_sha256=None if row[4] is None else str(row[4]),
            )
            for row in rows
        ),
    )


def clean_mixed_memory_revisions(
    repository: KnowledgeRepository,
    actor: ActorContext,
    request_id: str,
) -> tuple[str, ...]:
    return _clean_workspace_memory_revisions(repository, actor.workspace_id, request_id)


def _clean_workspace_memory_revisions(
    repository: KnowledgeRepository,
    workspace_id: str,
    request_id: str,
) -> tuple[str, ...]:
    with repository.connection() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT redaction.entity_id,head.revision_id
            FROM history_redactions AS redaction
            JOIN memory_heads AS head ON head.workspace_id=redaction.workspace_id
                AND head.document_id=redaction.entity_id
            WHERE redaction.request_id=? AND redaction.entity_kind='memory_document'
                AND redaction.current_revision_id IS NULL
            ORDER BY redaction.entity_id
            """,
            (request_id,),
        ).fetchall()
    clean_ids: list[str] = []
    for row in rows:
        document_id = str(row[0])
        previous_revision_id = str(row[1])
        clean_id = _publish_clean_memory_revision(
            repository,
            workspace_id,
            request_id,
            document_id,
            previous_revision_id,
        )
        clean_ids.append(clean_id)
    return tuple(clean_ids)


def purge_local_artifacts(repository: KnowledgeRepository, request_id: str) -> None:
    with repository.connection() as connection:
        rows = _PURGE_ARTIFACT_ROWS.validate_python(connection.execute(
            """
            SELECT ordinal,artifact_kind,entity_id,revision_id,relative_path,content_sha256,
                request.workspace_id
            FROM deletion_manifest_entries AS artifact
            JOIN deletion_requests AS request USING(request_id)
            WHERE artifact.request_id=? AND artifact.state='purge_pending' ORDER BY ordinal
            """,
            (request_id,),
        ).fetchall())
    for row in rows:
        ordinal = int(row[0])
        kind = str(row[1])
        entity_id = str(row[2])
        revision_id = str(row[3])
        relative_path = None if row[4] is None else str(row[4])
        content_digest = None if row[5] is None else str(row[5])
        if relative_path is not None:
            _purge_exact_file(repository.root, relative_path, content_digest)
        with repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            _scrub_manifest_entity(connection, str(row[6]), kind, entity_id, revision_id)
            _ = connection.execute(
                """
                UPDATE deletion_manifest_entries SET state='purged'
                WHERE request_id=? AND ordinal=?
                """,
                (request_id, ordinal),
            )


def pending_replica_requests(
    repository: KnowledgeRepository,
    request_id: str,
) -> tuple[ReplicaPurgeRequest, ...]:
    with repository.connection() as connection:
        rows = connection.execute(
            """
            SELECT replica.transfer_id,replica.system_id,replica.replica_id
            FROM transfer_replicas AS replica
            JOIN deletion_dependency_blocks AS block
                ON block.request_id=? AND block.dependency_kind='transfer'
                AND block.entity_id=replica.transfer_id
            WHERE replica.deletion_state!='purged'
            ORDER BY replica.transfer_id,replica.system_id,replica.replica_id
            """,
            (request_id,),
        ).fetchall()
    return tuple(
        ReplicaPurgeRequest(
            request_id=request_id,
            transfer_id=str(row[0]),
            system_id=str(row[1]),
            replica_id=str(row[2]),
        )
        for row in rows
    )


def record_replica_receipt(
    repository: KnowledgeRepository,
    receipt: ReplicaPurgeReceipt,
) -> None:
    receipt_digest = contract_sha256(receipt)
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        _ = connection.execute(
            """
            INSERT INTO replica_purge_receipts(
                request_id,transfer_id,system_id,replica_id,state,receipt_sha256,
                receipt_json,checked_at
            ) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(
                request_id,transfer_id,system_id,replica_id
            ) DO UPDATE SET state=excluded.state,receipt_sha256=excluded.receipt_sha256,
                receipt_json=excluded.receipt_json,checked_at=excluded.checked_at
            """,
            (
                receipt.request_id,
                receipt.transfer_id,
                receipt.system_id,
                receipt.replica_id,
                receipt.state,
                receipt_digest,
                receipt.model_dump_json(),
                receipt.checked_at.isoformat(),
            ),
        )
        _ = connection.execute(
            """
            UPDATE transfer_replicas SET deletion_state=?,receipt_sha256=?
            WHERE transfer_id=? AND system_id=? AND replica_id=?
            """,
            (
                receipt.state,
                receipt_digest,
                receipt.transfer_id,
                receipt.system_id,
                receipt.replica_id,
            ),
        )


def finish_purge_reconciliation(
    repository: KnowledgeRepository,
    request_id: str,
    clean_revision_ids: tuple[str, ...],
    updated_at: datetime,
) -> PurgeReceipt:
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        local_pending = int(
            connection.execute(
                """
                SELECT count(*) FROM deletion_manifest_entries
                WHERE request_id=? AND state!='purged'
                """,
                (request_id,),
            ).fetchone()[0]
        )
        replica_pending = int(
            connection.execute(
                """
                SELECT count(*) FROM transfer_replicas AS replica
                JOIN deletion_dependency_blocks AS block
                    ON block.request_id=? AND block.dependency_kind='transfer'
                    AND block.entity_id=replica.transfer_id
                WHERE replica.deletion_state!='purged'
                """,
                (request_id,),
            ).fetchone()[0]
        )
        state = (
            PurgeState.PURGED
            if local_pending == 0 and replica_pending == 0
            else PurgeState.LOCAL_PURGED
            if local_pending == 0
            else PurgeState.PURGE_PENDING
        )
        _ = connection.execute(
            "UPDATE deletion_requests SET state=?,updated_at=? WHERE request_id=?",
            (state.value, updated_at.isoformat(), request_id),
        )
        if state is PurgeState.PURGED:
            _ = connection.execute(
                """
                UPDATE tombstones SET state='purged' WHERE EXISTS (
                    SELECT 1 FROM deletion_dependency_blocks AS block
                    WHERE block.request_id=?
                        AND block.dependency_kind=tombstones.target_kind
                        AND block.entity_id=tombstones.target_id
                        AND block.revision_id=tombstones.target_revision_id
                )
                """,
                (request_id,),
            )
            _ = connection.execute(
                """
                UPDATE deletion_dependency_blocks SET state='purged' WHERE request_id=?
                """,
                (request_id,),
            )
        return _purge_receipt(connection, request_id, clean_revision_ids, updated_at)


def apply_erase_entries(
    repository: KnowledgeRepository,
    entries: tuple[EraseLedgerEntry, ...],
) -> int:
    applied = 0
    for entry in entries:
        with repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM tombstones WHERE operation_id=?",
                (entry.request_id,),
            ).fetchone()
            if existing is None:
                _insert_operation(
                    connection,
                    entry.request_id,
                    entry.workspace_id,
                    "restore_erase_apply",
                    entry.target,
                    entry.created_at,
                )
                _ = _block_target(
                    connection,
                    entry.workspace_id,
                    entry.target,
                    entry.request_id,
                    entry.created_at,
                    "purged",
                )
                applied += 1
            _record_memory_history_redactions(connection, entry.request_id, entry.workspace_id)
        _ = _clean_workspace_memory_revisions(repository, entry.workspace_id, entry.request_id)
        for artifact in entry.artifacts:
            if artifact.relative_path is not None:
                _purge_exact_file(
                    repository.root,
                    artifact.relative_path,
                    artifact.content_sha256,
                )
            with repository.connection() as connection:
                _ = connection.execute("BEGIN IMMEDIATE")
                _scrub_manifest_entity(
                    connection,
                    entry.workspace_id,
                    artifact.kind,
                    artifact.entity_id,
                    artifact.revision_id or "",
                )
    return applied


def memory_history_redaction(
    repository: KnowledgeRepository,
    workspace_id: str,
    document_id: str,
    revision_id: str,
) -> tuple[str, str | None] | None:
    with repository.connection() as connection:
        row = connection.execute(
            """
            SELECT request_id,current_revision_id FROM history_redactions
            WHERE workspace_id=? AND entity_kind='memory_document'
                AND entity_id=? AND revision_id=?
            """,
            (workspace_id, document_id, revision_id),
        ).fetchone()
    if row is None:
        return None
    return str(row[0]), None if row[1] is None else str(row[1])


def _target_scope(
    connection: sqlite3.Connection,
    workspace_id: str,
    target: EraseTarget,
) -> AccessScope:
    match target.kind:
        case "source":
            query = "SELECT scope.scope_json FROM sources JOIN access_scopes AS scope USING(scope_key) WHERE sources.workspace_id=? AND source_id=?"
        case "page":
            query = "SELECT scope.scope_json FROM wiki_pages JOIN access_scopes AS scope USING(scope_key) WHERE wiki_pages.workspace_id=? AND page_id=?"
        case "claim":
            query = "SELECT scope.scope_json FROM claim_locations AS location JOIN wiki_pages AS page ON page.workspace_id=location.workspace_id AND page.page_id=location.page_id JOIN access_scopes AS scope ON scope.scope_key=page.scope_key WHERE location.workspace_id=? AND location.claim_id=? ORDER BY location.is_current DESC LIMIT 1"
        case "memory_document":
            query = "SELECT scope.scope_json FROM memory_documents AS document JOIN access_scopes AS scope ON scope.scope_key=document.scope_key WHERE document.workspace_id=? AND document.document_id=?"
        case "memory_entry":
            query = "SELECT scope.scope_json FROM memory_entries AS entry JOIN access_scopes AS scope ON scope.scope_key=entry.scope_key WHERE entry.workspace_id=? AND entry.entry_id=? ORDER BY entry.memory_revision_id DESC LIMIT 1"
        case "transfer":
            row = connection.execute(
                "SELECT 1 FROM context_transfers WHERE workspace_id=? AND transfer_id=?",
                (workspace_id, target.entity_id),
            ).fetchone()
            if row is None:
                raise DeletionError("deletion_target_not_found", target.entity_id)
            return AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=workspace_id)
        case unreachable:
            raise DeletionError("deletion_target_kind_invalid", str(unreachable))
    row = connection.execute(query, (workspace_id, target.entity_id)).fetchone()
    if row is None:
        raise DeletionError("deletion_target_not_found", target.entity_id)
    return AccessScope.model_validate_json(str(row[0]))


def _block_target(
    connection: sqlite3.Connection,
    workspace_id: str,
    target: EraseTarget,
    operation_id: str,
    occurred_at: datetime,
    state: str,
) -> str:
    target_revision = target.revision_id or ""
    tombstone_id = "tombstone." + sha256(
        f"{workspace_id}\x1f{target.kind}\x1f{target.entity_id}\x1f{target_revision}".encode()
    ).hexdigest()[:48]
    sequence = int(
        connection.execute(
            "SELECT coalesce(max(sequence),0)+1 FROM tombstones WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()[0]
    )
    _ = connection.execute(
        """
        INSERT INTO tombstones(
            tombstone_id,workspace_id,target_kind,target_id,target_revision_id,
            sequence,operation_id,state,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(
            workspace_id,target_kind,target_id,target_revision_id
        ) DO UPDATE SET state=CASE
            WHEN tombstones.state='purged' THEN 'purged' ELSE excluded.state END,
            operation_id=excluded.operation_id,sequence=excluded.sequence
        """,
        (
            tombstone_id,
            workspace_id,
            target.kind,
            target.entity_id,
            target_revision,
            sequence,
            operation_id,
            state,
            occurred_at.isoformat(),
        ),
    )
    if target.kind == "source":
        _ = connection.execute(
            "UPDATE sources SET visibility='blocked' WHERE workspace_id=? AND source_id=?",
            (workspace_id, target.entity_id),
        )
    blocked_nodes = _dependent_nodes(connection, workspace_id, target)
    for kind, entity_id, revision_id in blocked_nodes:
        dependent_target_kind = "claim" if kind == "claim" else "memory_entry"
        dependent_id = "tombstone." + sha256(
            f"{workspace_id}\x1f{dependent_target_kind}\x1f{entity_id}\x1f{revision_id}".encode()
        ).hexdigest()[:48]
        sequence += 1
        _ = connection.execute(
            """
            INSERT OR IGNORE INTO tombstones(
                tombstone_id,workspace_id,target_kind,target_id,target_revision_id,
                sequence,operation_id,state,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                dependent_id,
                workspace_id,
                dependent_target_kind,
                entity_id,
                revision_id,
                sequence,
                operation_id,
                state,
                occurred_at.isoformat(),
            ),
        )
    ids = tuple({target.entity_id, *(item[1] for item in blocked_nodes)})
    page_ids = tuple(
        {
            str(row[0])
            for entity_id in ids
            for row in connection.execute(
                "SELECT page_id FROM claim_locations WHERE workspace_id=? AND claim_id=?",
                (workspace_id, entity_id),
            ).fetchall()
        }
    )
    for entity_id in (*ids, *page_ids):
        _ = connection.execute(
            """
            UPDATE context_transfers SET state=? WHERE workspace_id=? AND transfer_id IN (
                SELECT dependency.transfer_id FROM transfer_dependencies AS dependency
                WHERE dependency.entity_id=?
            ) AND state NOT IN ('purged','expired')
            """,
            (state, workspace_id, entity_id),
        )
    _remove_blocked_chunks(connection, workspace_id, target, blocked_nodes)
    return tombstone_id


def _dependent_nodes(
    connection: sqlite3.Connection,
    workspace_id: str,
    target: EraseTarget,
) -> tuple[tuple[str, str, str], ...]:
    seed_ids: list[str] = [target.entity_id]
    if target.kind == "source":
        seed_ids.extend(
            str(row[0])
            for row in connection.execute(
                "SELECT segment_id FROM segments WHERE workspace_id=? AND source_id=?",
                (workspace_id, target.entity_id),
            ).fetchall()
        )
        seed_ids.extend(source_conversation_event_ids(connection, workspace_id, target.entity_id))
    placeholders = ",".join("?" for _ in seed_ids)
    rows = connection.execute(
        f"""
        WITH RECURSIVE reachable(node_id) AS (
            SELECT node_id FROM evidence_nodes
            WHERE workspace_id=? AND entity_id IN ({placeholders})
            UNION
            SELECT edge.from_node_id FROM evidence_edges AS edge
            JOIN reachable ON edge.to_node_id=reachable.node_id
        )
        SELECT DISTINCT node.entity_kind,node.entity_id,node.revision_id
        FROM reachable JOIN evidence_nodes AS node USING(node_id)
        WHERE node.entity_kind IN ('claim','memory_entry')
        ORDER BY node.entity_kind,node.entity_id,node.revision_id
        """,
        (workspace_id, *seed_ids),
    ).fetchall()
    values = {(str(row[0]), str(row[1]), str(row[2])) for row in rows}
    example_rows = connection.execute(
        """
        SELECT DISTINCT entry.entry_id,entry.memory_revision_id
        FROM memory_entries AS entry,json_each(entry.entry_json,'$.example_refs') AS example
        WHERE entry.workspace_id=? AND (
            json_extract(example.value,'$.page_id')=?
            OR json_extract(example.value,'$.claim_id')=?
        )
        """,
        (workspace_id, target.entity_id, target.entity_id),
    ).fetchall()
    values.update(("memory_entry", str(row[0]), str(row[1])) for row in example_rows)
    if target.kind == "memory_entry":
        values.add(("memory_entry", target.entity_id, target.revision_id or ""))
    return tuple(sorted(values))


def _remove_blocked_chunks(
    connection: sqlite3.Connection,
    workspace_id: str,
    target: EraseTarget,
    blocked_nodes: tuple[tuple[str, str, str], ...],
) -> None:
    entity_ids = {target.entity_id, *(item[1] for item in blocked_nodes)}
    page_ids = {
        str(row[0])
        for entity_id in entity_ids
        for row in connection.execute(
            "SELECT page_id FROM claim_locations WHERE workspace_id=? AND claim_id=?",
            (workspace_id, entity_id),
        ).fetchall()
    }
    chunk_ids = [
        str(row[0])
        for entity_id in (*entity_ids, *page_ids)
        for row in connection.execute(
            "SELECT chunk_id FROM chunks WHERE workspace_id=? AND entity_id=?",
            (workspace_id, entity_id),
        ).fetchall()
    ]
    for chunk_id in sorted(set(chunk_ids)):
        _ = connection.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (chunk_id,))
        _ = connection.execute("DELETE FROM chunks WHERE chunk_id=?", (chunk_id,))


def _manifest_entries(
    connection: sqlite3.Connection,
    workspace_id: str,
    target: EraseTarget,
) -> tuple[PurgeManifestEntry, ...]:
    entity_ids = {target.entity_id}
    dependent_nodes = _dependent_nodes(connection, workspace_id, target)
    entity_ids.update(item[1] for item in dependent_nodes)
    values: set[tuple[str, str, str, str | None, str | None]] = set()
    if target.kind == "source":
        for row in connection.execute(
            "SELECT source_id,revision_id,relative_path,sha256 FROM source_files WHERE workspace_id=? AND source_id=?",
            (workspace_id, target.entity_id),
        ).fetchall():
            values.add(("source_file", str(row[0]), str(row[1]), str(row[2]), str(row[3])))
        for row in connection.execute(
            "SELECT segment_id,revision_id,content_sha256 FROM segments WHERE workspace_id=? AND source_id=?",
            (workspace_id, target.entity_id),
        ).fetchall():
            values.add(("segment", str(row[0]), str(row[1]), None, str(row[2])))
    page_ids = set()
    if target.kind == "page":
        page_ids.add(target.entity_id)
    claim_page_revisions: set[tuple[str, str]] = set()
    for entity_id in entity_ids:
        claim_page_revisions.update(
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT page_id,page_revision_id FROM claim_locations WHERE workspace_id=? AND claim_id=?",
                (workspace_id, entity_id),
            ).fetchall()
        )
    page_ids.update(item[0] for item in claim_page_revisions)
    for page_id in page_ids:
        revision_rows = connection.execute(
            "SELECT revision_id,relative_path,body_sha256 FROM knowledge_revisions WHERE workspace_id=? AND page_id=?",
            (workspace_id, page_id),
        ).fetchall()
        for row in revision_rows:
            if (
                target.kind != "page"
                and (page_id, str(row[0])) not in claim_page_revisions
            ):
                continue
            values.add(("knowledge_revision_file", page_id, str(row[0]), str(row[1]), str(row[2])))
    for kind, entity_id, revision_id in dependent_nodes:
        if kind == "claim":
            digest_row = connection.execute(
                "SELECT statement_sha256 FROM claim_versions WHERE workspace_id=? AND claim_id=? AND revision_id=?",
                (workspace_id, entity_id, revision_id),
            ).fetchone()
            values.add(
                (
                    "claim_record",
                    entity_id,
                    revision_id,
                    None,
                    None if digest_row is None else str(digest_row[0]),
                )
            )
        if kind == "memory_entry":
            values.add(("memory_entry_record", entity_id, revision_id, None, None))
    memory_ids = set()
    if target.kind == "memory_document":
        memory_ids.add(target.entity_id)
    memory_revision_pairs: set[tuple[str, str]] = set()
    for entity_id in entity_ids:
        memory_revision_pairs.update(
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT DISTINCT document_id,memory_revision_id FROM memory_entries WHERE workspace_id=? AND entry_id=?",
                (workspace_id, entity_id),
            ).fetchall()
        )
    memory_ids.update(item[0] for item in memory_revision_pairs)
    for document_id in memory_ids:
        revision_rows = connection.execute(
            "SELECT revision_id,relative_path,body_sha256 FROM memory_revisions WHERE workspace_id=? AND document_id=?",
            (workspace_id, document_id),
        ).fetchall()
        for row in revision_rows:
            if (
                target.kind != "memory_document"
                and (document_id, str(row[0])) not in memory_revision_pairs
            ):
                continue
            values.add(("memory_revision_file", document_id, str(row[0]), str(row[1]), str(row[2])))
    transfer_ids = {
        str(row[0])
        for entity_id in (*entity_ids, *page_ids, *memory_ids)
        for row in connection.execute(
            "SELECT DISTINCT transfer_id FROM transfer_dependencies WHERE entity_id=?",
            (entity_id,),
        ).fetchall()
    }
    if target.kind == "transfer":
        transfer_ids.add(target.entity_id)
    for transfer_id in transfer_ids:
        values.add(("context_transfer", transfer_id, "", None, None))
        for row in connection.execute(
            "SELECT system_id,replica_id FROM transfer_replicas WHERE transfer_id=?",
            (transfer_id,),
        ).fetchall():
            values.add(("replica", str(row[1]), transfer_id, None, None))
    for entity_id in (*entity_ids, *page_ids, *memory_ids):
        for row in connection.execute(
            "SELECT chunk_id,revision_id,content_sha256 FROM chunks WHERE workspace_id=? AND entity_id=?",
            (workspace_id, entity_id),
        ).fetchall():
            values.add(("search_chunk", str(row[0]), str(row[1]), None, str(row[2])))
    return tuple(
        PurgeManifestEntry(
            kind=kind,
            entity_id=entity_id,
            revision_id=revision_id or None,
            relative_path=relative_path,
            content_sha256=content_digest,
        )
        for kind, entity_id, revision_id, relative_path, content_digest in sorted(values)
    )


def _persist_dependency_blocks(
    connection: sqlite3.Connection,
    request_id: str,
    workspace_id: str,
) -> None:
    operation_targets = connection.execute(
        "SELECT target_kind,target_id,target_revision_id FROM tombstones WHERE workspace_id=? AND operation_id=?",
        (workspace_id, request_id),
    ).fetchall()
    for row in operation_targets:
        _ = connection.execute(
            "INSERT OR IGNORE INTO deletion_dependency_blocks VALUES (?,?,?,?, 'purge_pending')",
            (request_id, str(row[0]), str(row[1]), str(row[2])),
        )
    entity_ids = [str(row[1]) for row in operation_targets]
    for entity_id in entity_ids:
        for row in connection.execute(
            "SELECT DISTINCT transfer_id FROM transfer_dependencies WHERE entity_id=?",
            (entity_id,),
        ).fetchall():
            transfer_id = str(row[0])
            _ = connection.execute(
                "INSERT OR IGNORE INTO deletion_dependency_blocks VALUES (?,'transfer',?,'','purge_pending')",
                (request_id, transfer_id),
            )
            _ = connection.execute(
                "UPDATE context_transfers SET state='purge_pending' WHERE transfer_id=? AND state NOT IN ('purged','expired')",
                (transfer_id,),
            )


def _mark_replica_purge_pending(
    connection: sqlite3.Connection,
    request_id: str,
    workspace_id: str,
) -> None:
    _ = workspace_id
    _ = connection.execute(
        """
        UPDATE transfer_replicas SET deletion_state='purge_pending'
        WHERE transfer_id IN (
            SELECT entity_id FROM deletion_dependency_blocks
            WHERE request_id=? AND dependency_kind='transfer'
        ) AND deletion_state!='purged'
        """,
        (request_id,),
    )


def _record_memory_history_redactions(
    connection: sqlite3.Connection,
    request_id: str,
    workspace_id: str,
) -> None:
    rows = connection.execute(
        """
        SELECT DISTINCT entry.document_id,entry.memory_revision_id
        FROM memory_entries AS entry
        JOIN tombstones AS tomb ON tomb.workspace_id=entry.workspace_id
            AND tomb.target_kind='memory_entry' AND tomb.target_id=entry.entry_id
            AND (tomb.target_revision_id='' OR tomb.target_revision_id=entry.memory_revision_id)
        WHERE entry.workspace_id=? AND tomb.operation_id=?
        """,
        (workspace_id, request_id),
    ).fetchall()
    for row in rows:
        _ = connection.execute(
            """
            INSERT OR IGNORE INTO history_redactions(
                workspace_id,entity_kind,entity_id,revision_id,request_id,current_revision_id
            ) VALUES (?,'memory_document',?,?,?,NULL)
            """,
            (workspace_id, str(row[0]), str(row[1]), request_id),
        )


def _publish_clean_memory_revision(
    repository: KnowledgeRepository,
    workspace_id: str,
    request_id: str,
    document_id: str,
    previous_revision_id: str,
) -> str:
    clean_id = "clean." + sha256(
        f"{request_id}\x1f{document_id}\x1f{previous_revision_id}".encode()
    ).hexdigest()[:48]
    with repository.connection() as connection:
        existing = connection.execute(
            "SELECT 1 FROM memory_revisions WHERE workspace_id=? AND document_id=? AND revision_id=?",
            (workspace_id, document_id, clean_id),
        ).fetchone()
        if existing is not None:
            return clean_id
        document_row = connection.execute(
            "SELECT document_json FROM memory_documents WHERE workspace_id=? AND document_id=?",
            (workspace_id, document_id),
        ).fetchone()
        entry_rows = connection.execute(
            """
            SELECT entry.entry_json FROM memory_entries AS entry
            WHERE entry.workspace_id=? AND entry.document_id=?
                AND entry.memory_revision_id=? AND NOT EXISTS (
                    SELECT 1 FROM tombstones AS tomb
                    WHERE tomb.workspace_id=entry.workspace_id
                        AND tomb.target_kind='memory_entry'
                        AND tomb.target_id=entry.entry_id
                        AND (tomb.target_revision_id='' OR tomb.target_revision_id=entry.memory_revision_id)
                        AND tomb.state IN ('blocked','purge_pending','purged')
                ) ORDER BY entry.entry_id
            """,
            (workspace_id, document_id, previous_revision_id),
        ).fetchall()
        constraint_rows = connection.execute(
            """
            SELECT binding.binding_json FROM constraint_bindings AS binding
            WHERE binding.workspace_id=? AND binding.document_id=?
                AND binding.memory_revision_id=? AND EXISTS (
                    SELECT 1 FROM memory_entries AS entry
                    WHERE entry.workspace_id=binding.workspace_id
                        AND entry.document_id=binding.document_id
                        AND entry.memory_revision_id=binding.memory_revision_id
                        AND entry.entry_id=binding.entry_id
                        AND NOT EXISTS (
                            SELECT 1 FROM tombstones AS tomb
                            WHERE tomb.workspace_id=entry.workspace_id
                                AND tomb.target_kind='memory_entry'
                                AND tomb.target_id=entry.entry_id
                                AND tomb.state IN ('blocked','purge_pending','purged')
                        )
                ) ORDER BY binding.constraint_id
            """,
            (workspace_id, document_id, previous_revision_id),
        ).fetchall()
    if document_row is None:
        raise DeletionError("memory_document_not_found", document_id)
    entries = tuple(MemoryEntry.model_validate_json(str(row[0])) for row in entry_rows)
    body = _clean_memory_body(entries)
    body_sha256 = sha256(body).hexdigest()
    previous_document = MemoryDocument.model_validate_json(str(document_row[0]))
    document = previous_document.model_copy(update={"head_revision_id": clean_id})
    revision = MemoryRevision(
        document_id=document_id,
        revision_id=clean_id,
        previous_revision_id=previous_revision_id,
        body_sha256=body_sha256,
        entry_ids=tuple(entry.entry_id for entry in entries),
        created_at=datetime.now(UTC),
    )
    operation_id = clean_id
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id=operation_id,
            target=MemoryRevisionTarget(
                workspace_id=workspace_id,
                document_id=document_id,
                revision_id=clean_id,
            ),
            content=body,
            sha256=body_sha256,
        )
    )
    published = repository.files.publish(prepared)
    write = MemoryRevisionWrite(
        document=document,
        revision=revision,
        expected=HeadExpectation(
            entity_id=document_id,
            expected_revision_id=previous_revision_id,
            resulting_revision_id=clean_id,
        ),
        prepared_file=prepared,
        entries=entries,
        constraints=tuple(
            ConstraintBinding.model_validate_json(str(row[0])) for row in constraint_rows
        ),
    )
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        assert_memory_head(connection, workspace_id, write)
        insert_memory_revision(connection, operation_id, write, published)
        _ = connection.execute(
            """
            UPDATE history_redactions SET current_revision_id=?
            WHERE request_id=? AND workspace_id=? AND entity_kind='memory_document'
                AND entity_id=? AND current_revision_id IS NULL
            """,
            (clean_id, request_id, workspace_id, document_id),
        )
    return clean_id


def _clean_memory_body(entries: tuple[MemoryEntry, ...]) -> bytes:
    parts = ["# Memory\n"]
    for entry in entries:
        parts.extend((f"\n## {entry.entry_id}\n\n", entry.text, "\n"))
    return "".join(parts).encode()


def _purge_exact_file(root: Path, relative_path: str, expected_sha256: str | None) -> None:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise DeletionError("purge_path_outside_root", relative_path)
    path = root.joinpath(*relative.parts)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DeletionError("purge_path_type_unsafe", relative_path)
    if expected_sha256 is not None:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            digest = sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
        finally:
            os.close(descriptor)
        if digest.hexdigest() != expected_sha256:
            raise DeletionError("purge_file_digest_mismatch", relative_path)
    path.unlink()
    directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _scrub_manifest_entity(
    connection: sqlite3.Connection,
    workspace_id: str,
    kind: str,
    entity_id: str,
    revision_id: str,
) -> None:
    match kind:
        case "source_file":
            scrub_source_conversation_events(connection, workspace_id, entity_id)
            _ = connection.execute(
                "DELETE FROM source_files WHERE workspace_id=? AND source_id=? AND revision_id=?",
                (workspace_id, entity_id, revision_id),
            )
            _ = connection.execute(
                "UPDATE sources SET source_json=? WHERE workspace_id=? AND source_id=?",
                (_REDACTED_JSON, workspace_id, entity_id),
            )
            _ = connection.execute(
                "UPDATE source_revisions SET revision_json=? WHERE workspace_id=? AND "
                "source_id=? AND revision_id=?",
                (_REDACTED_JSON, workspace_id, entity_id, revision_id),
            )
        case "segment":
            _ = connection.execute(
                "UPDATE segments SET segment_json=?,locator_json=? WHERE workspace_id=? AND "
                "segment_id=? AND revision_id=?",
                (_REDACTED_JSON, _REDACTED_JSON, workspace_id, entity_id, revision_id),
            )
        case "knowledge_revision_file":
            _ = connection.execute(
                "UPDATE knowledge_revisions SET revision_json=? WHERE workspace_id=? AND "
                "page_id=? AND revision_id=?",
                (_REDACTED_JSON, workspace_id, entity_id, revision_id),
            )
        case "memory_revision_file":
            _ = connection.execute(
                "UPDATE memory_entries SET entry_json=? WHERE workspace_id=? AND "
                "document_id=? AND memory_revision_id=? AND EXISTS (SELECT 1 FROM tombstones "
                "AS tomb WHERE tomb.workspace_id=memory_entries.workspace_id AND "
                "tomb.target_kind='memory_entry' AND tomb.target_id=memory_entries.entry_id)",
                (_REDACTED_JSON, workspace_id, entity_id, revision_id),
            )
        case "claim_record":
            _ = connection.execute(
                "UPDATE claim_versions SET claim_json=? WHERE workspace_id=? AND claim_id=? "
                "AND revision_id=?",
                (_REDACTED_JSON, workspace_id, entity_id, revision_id),
            )
        case "memory_entry_record":
            _ = connection.execute(
                "UPDATE memory_entries SET entry_json=? WHERE workspace_id=? AND entry_id=? "
                "AND memory_revision_id=?",
                (_REDACTED_JSON, workspace_id, entity_id, revision_id),
            )
        case "search_chunk":
            _ = connection.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (entity_id,))
            _ = connection.execute("DELETE FROM chunks WHERE chunk_id=?", (entity_id,))
        case "context_transfer":
            _ = connection.execute(
                "UPDATE context_transfers SET transfer_json=?,state='purge_pending' WHERE "
                "workspace_id=? AND transfer_id=?",
                (_REDACTED_JSON, workspace_id, entity_id),
            )
        case "replica":
            pass
        case unreachable:
            raise DeletionError("purge_manifest_kind_invalid", str(unreachable))


def _insert_operation(
    connection: sqlite3.Connection,
    operation_id: str,
    workspace_id: str,
    operation_kind: str,
    target: EraseTarget,
    occurred_at: datetime,
) -> None:
    payload_sha256 = contract_sha256(target)
    receipt_json = (
        f'{{"operation_id":"{operation_id}","status":"applied",'
        f'"target_kind":"{target.kind}","target_id":"{target.entity_id}"}}'
    )
    receipt_sha256 = sha256(receipt_json.encode()).hexdigest()
    _ = connection.execute(
        """
        INSERT OR IGNORE INTO operations(
            operation_id,workspace_id,operation_kind,payload_sha256,status,
            receipt_sha256,receipt_json,created_at,committed_at
        ) VALUES (?,?,?,?, 'applied',?,?,?,?)
        """,
        (
            operation_id,
            workspace_id,
            operation_kind,
            payload_sha256,
            receipt_sha256,
            receipt_json,
            occurred_at.isoformat(),
            occurred_at.isoformat(),
        ),
    )


def _retraction_receipt(
    connection: sqlite3.Connection,
    request: RetractionRequest,
    tombstone_id: str,
    occurred_at: str,
) -> RetractionReceipt:
    dependency_ids = tuple(
        sorted(
            {
                str(row[0])
                for row in connection.execute(
                    "SELECT target_id FROM tombstones WHERE workspace_id=? AND operation_id=? AND target_id!=?",
                    (request.actor.workspace_id, request.operation_id, request.target.entity_id),
                ).fetchall()
            }
        )
    )
    transfer_ids = tuple(
        sorted(
            {
                str(row[0])
                for entity_id in (request.target.entity_id, *dependency_ids)
                for row in connection.execute(
                    "SELECT transfer_id FROM transfer_dependencies WHERE entity_id=?",
                    (entity_id,),
                ).fetchall()
            }
        )
    )
    return RetractionReceipt(
        operation_id=request.operation_id,
        workspace_id=request.actor.workspace_id,
        tombstone_id=tombstone_id,
        target=request.target,
        blocked_dependency_ids=dependency_ids,
        blocked_transfer_ids=transfer_ids,
        occurred_at=occurred_at,
    )


def _purge_receipt(
    connection: sqlite3.Connection,
    request_id: str,
    clean_revision_ids: tuple[str, ...],
    updated_at: datetime,
) -> PurgeReceipt:
    row = connection.execute(
        """
        SELECT workspace_id,state,ledger_sequence,ledger_entry_sha256
        FROM deletion_requests WHERE request_id=?
        """,
        (request_id,),
    ).fetchone()
    if row is None:
        raise DeletionError("purge_request_not_found", request_id)
    local_pending = int(
        connection.execute(
            "SELECT count(*) FROM deletion_manifest_entries WHERE request_id=? AND state!='purged'",
            (request_id,),
        ).fetchone()[0]
    )
    replica_pending = int(
        connection.execute(
            """
            SELECT count(*) FROM transfer_replicas AS replica
            JOIN deletion_dependency_blocks AS block
                ON block.request_id=? AND block.dependency_kind='transfer'
                AND block.entity_id=replica.transfer_id
            WHERE replica.deletion_state!='purged'
            """,
            (request_id,),
        ).fetchone()[0]
    )
    return PurgeReceipt(
        request_id=request_id,
        workspace_id=str(row[0]),
        state=PurgeState(str(row[1])),
        ledger_sequence=int(row[2]),
        ledger_entry_sha256=str(row[3]),
        local_pending=local_pending,
        replica_pending=replica_pending,
        clean_revision_ids=clean_revision_ids,
        updated_at=updated_at,
    )


from ads_booster.knowledge.scope_contracts import ActorContext


__all__ = [
    "apply_erase_entries",
    "clean_mixed_memory_revisions",
    "existing_purge_receipt",
    "finish_purge_reconciliation",
    "memory_history_redaction",
    "pending_replica_requests",
    "persist_purge_request",
    "prepare_purge_manifest",
    "purge_local_artifacts",
    "read_purge_manifest",
    "record_replica_receipt",
    "require_purge_authority",
    "require_reconcile_authority",
    "retract_target",
]

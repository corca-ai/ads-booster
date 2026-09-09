from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import (
    AccessScope,
    KnowledgeOperation,
    MemoryOperation,
    OperationReceipt,
    ScopeKind,
)
from ads_booster.knowledge.file_store import (
    KnowledgeRevisionTarget,
    MemoryRevisionTarget,
    SkillRevisionTarget,
)
from ads_booster.knowledge.grant_policy import authorize_write
from ads_booster.knowledge.repository_dependency import append_dependency_invalidations
from ads_booster.knowledge.repository_memory import (
    assert_memory_head,
    insert_memory_revision,
    insert_memory_shell,
)
from ads_booster.knowledge.repository_page import (
    assert_page_head,
    insert_page_revision,
    insert_page_shell,
)
from ads_booster.knowledge.repository_skills import (
    assert_skill_head,
    insert_skill_revision,
)
from ads_booster.knowledge.repository_source import (
    _insert_index,
    _insert_job,
    _insert_operation,
    _operation_receipt_in,
    _require_read,
)
from ads_booster.knowledge.repository_types import (
    CatalogCommit,
    HeadExpectation,
    IndexOutboxItem,
    MemoryRevisionWrite,
    PageRevisionWrite,
    RepositoryCommitBoundary,
    conflict,
)
from ads_booster.knowledge.skill_contracts import SkillOperation

if TYPE_CHECKING:
    from ads_booster.knowledge.file_store import PublishedRevisionFile, RevisionTarget
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository

_OPTIONAL_STRING_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


def commit_catalog(
    repository: KnowledgeRepository,
    command: CatalogCommit,
) -> OperationReceipt:
    _validate_commit(command)
    workspace_id = _workspace_id(command)
    with repository.connection() as connection:
        _authorize_commit(connection, command)
    repository.reach_commit_boundary(RepositoryCommitBoundary.BEFORE_FILE_PUBLISH)
    published = {
        item.target: item
        for item in (
            repository.files.publish(prepared)
            for write in (*command.page_writes, *command.memory_writes, *command.skill_writes)
            for prepared in (write.prepared_file,)
            if prepared is not None
        )
    }
    repository.reach_commit_boundary(RepositoryCommitBoundary.AFTER_FILE_PUBLISH)
    try:
        with repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            _authorize_commit(connection, command)
            replay = _operation_receipt_in(
                connection,
                command.operation_id,
                command.payload_sha256,
            )
            if replay is not None:
                return OperationReceipt.model_validate_json(replay)
            _assert_heads(connection, workspace_id, command)
            _insert_operation(
                connection,
                (
                    command.operation_id,
                    workspace_id,
                    _operation_kind(command),
                    command.payload_sha256,
                    command.receipt.model_dump_json(),
                    command.receipt.occurred_at.isoformat(),
                ),
            )
            _insert_shells(connection, workspace_id, command)
            _insert_revisions(connection, workspace_id, command, published)
            append_dependency_invalidations(
                connection,
                workspace_id,
                command.operation_id,
                command.dependency_invalidations,
            )
            _insert_audit(connection, command)
            _insert_indexes(connection, workspace_id, command)
            for job in command.jobs:
                _insert_job(connection, job.job, job.unique_key)
            _insert_redirects(connection, workspace_id, command)
            repository.reach_commit_boundary(RepositoryCommitBoundary.BEFORE_DB_COMMIT)
            connection.commit()
            repository.reach_commit_boundary(RepositoryCommitBoundary.AFTER_DB_COMMIT)
    except sqlite3.IntegrityError as error:
        conflict("catalog_integrity_conflict", command.operation_id).with_traceback(
            error.__traceback__
        )
    return command.receipt


def _authorize_commit(connection: sqlite3.Connection, command: CatalogCommit) -> None:
    _require_read(connection, command.actor)
    at = datetime.now(UTC)
    for write in command.page_writes:
        _ = authorize_write(actor=command.actor, target_scope=write.page.scope, at=at)
    for write in command.memory_writes:
        target_scope = AccessScope(
            kind=ScopeKind.WORKSPACE,
            workspace_id=write.document.workspace_id,
        )
        _ = authorize_write(actor=command.actor, target_scope=target_scope, at=at)
    if command.skill_writes:
        target_scope = AccessScope(
            kind=ScopeKind.WORKSPACE,
            workspace_id=command.actor.workspace_id,
        )
        _ = authorize_write(actor=command.actor, target_scope=target_scope, at=at)
    for invalidation in command.dependency_invalidations:
        row = _OPTIONAL_STRING_ROW.validate_python(
            connection.execute(
                """
                SELECT scope.scope_json FROM evidence_nodes AS node
                JOIN access_scopes AS scope ON scope.scope_key=node.scope_key
                WHERE node.workspace_id=? AND node.entity_kind=? AND node.entity_id=?
                AND node.revision_id=? AND node.segment_id=''
                """,
                (
                    command.actor.workspace_id,
                    invalidation.upstream_kind,
                    invalidation.upstream_id,
                    invalidation.upstream_revision_id,
                ),
            ).fetchone()
        )
        if row is None:
            conflict("dependency_upstream_missing", invalidation.upstream_id)
        target_scope = AccessScope.model_validate_json(row[0])
        _ = authorize_write(actor=command.actor, target_scope=target_scope, at=at)


def _assert_heads(
    connection: sqlite3.Connection,
    workspace_id: str,
    command: CatalogCommit,
) -> None:
    for write in command.page_writes:
        assert_page_head(connection, workspace_id, write)
    for write in command.memory_writes:
        assert_memory_head(connection, workspace_id, write)
    for write in command.skill_writes:
        assert_skill_head(connection, workspace_id, write)


def _insert_shells(
    connection: sqlite3.Connection,
    workspace_id: str,
    command: CatalogCommit,
) -> None:
    for write in command.page_writes:
        insert_page_shell(connection, workspace_id, write)
    for write in command.memory_writes:
        insert_memory_shell(connection, write)


def _insert_revisions(
    connection: sqlite3.Connection,
    workspace_id: str,
    command: CatalogCommit,
    published: dict[RevisionTarget, PublishedRevisionFile],
) -> None:
    for write in command.page_writes:
        target = KnowledgeRevisionTarget(
            page_id=write.page.page_id,
            revision_id=write.revision.revision_id,
        )
        insert_page_revision(
            connection,
            workspace_id,
            command.operation_id,
            write,
            _published(published, target),
        )
    for write in command.memory_writes:
        target = MemoryRevisionTarget(
            workspace_id=workspace_id,
            document_id=write.document.document_id,
            revision_id=write.revision.revision_id,
        )
        insert_memory_revision(
            connection,
            command.operation_id,
            write,
            _published(published, target),
        )
    for write in command.skill_writes:
        target = (
            None
            if write.record is None
            else SkillRevisionTarget(
                workspace_id=workspace_id,
                skill_id=write.record.skill_id,
                revision_id=write.record.version,
            )
        )
        item = None if target is None else _published(published, target)
        insert_skill_revision(
            connection,
            workspace_id,
            command.operation_id,
            write,
            item,
        )


def _insert_indexes(
    connection: sqlite3.Connection,
    workspace_id: str,
    command: CatalogCommit,
) -> None:
    supplied = {(item.entity_kind, item.entity_id) for item in command.index_items}
    for item in command.index_items:
        _insert_index(connection, item)
    for write in command.page_writes:
        if ("page", write.page.page_id) not in supplied:
            _insert_index(connection, _page_index(command.operation_id, workspace_id, write))
    for write in command.memory_writes:
        if ("memory", write.document.document_id) not in supplied:
            _insert_index(connection, _memory_index(command.operation_id, workspace_id, write))


def _validate_commit(command: CatalogCommit) -> None:
    if command.receipt.operation_id != command.operation_id:
        conflict("operation_receipt_mismatch", command.operation_id)
    writes = (*command.page_writes, *command.memory_writes, *command.skill_writes)
    resulting = tuple(write.expected.resulting_revision_id for write in writes)
    if tuple(command.receipt.resulting_revision_ids) != resulting:
        conflict("operation_result_mismatch", command.operation_id)
    if not writes and not command.dependency_invalidations:
        conflict("catalog_commit_empty", command.operation_id)
    for record in command.operation_records:
        if record.operation_id != command.operation_id:
            conflict("operation_record_mismatch", command.operation_id)
    if any(write.operation not in command.operation_records for write in command.skill_writes):
        conflict("skill_operation_record_mismatch", command.operation_id)
    for invalidation in command.dependency_invalidations:
        if (
            invalidation.upstream_kind != "memory_entry"
            or invalidation.resulting_state.value not in {"stale", "restricted"}
            or not invalidation.reason.strip()
        ):
            conflict("dependency_invalidation_invalid", invalidation.upstream_id)


def _workspace_id(command: CatalogCommit) -> str:
    workspace_ids = {
        *(write.page.scope.workspace_id for write in command.page_writes),
        *(write.document.workspace_id for write in command.memory_writes),
        *(command.actor.workspace_id for _ in command.skill_writes),
    }
    if not workspace_ids and command.dependency_invalidations:
        workspace_ids.add(command.actor.workspace_id)
    if len(workspace_ids) != 1:
        conflict("catalog_workspace_conflict", command.operation_id)
    return next(iter(workspace_ids))


def _operation_kind(command: CatalogCommit) -> str:
    if not command.operation_records:
        return "catalog_commit"
    return "+".join(type(item).__name__ for item in command.operation_records)


def _insert_audit(connection: sqlite3.Connection, command: CatalogCommit) -> None:
    for write in command.page_writes:
        _insert_head(connection, command.operation_id, "page", write.expected)
    for write in command.memory_writes:
        _insert_head(connection, command.operation_id, "memory", write.expected)
    for ordinal, record in enumerate(command.operation_records):
        match record:
            case KnowledgeOperation(target_page_ids=targets):
                target_id = ",".join(targets)
            case MemoryOperation(document_id=document_id):
                target_id = document_id
            case SkillOperation(skill_id=skill_id):
                target_id = skill_id
        _ = connection.execute(
            """
            INSERT INTO operation_records(
                operation_id,ordinal,record_kind,target_id,record_sha256,record_json
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                command.operation_id,
                ordinal,
                type(record).__name__,
                target_id,
                contract_sha256(record),
                record.model_dump_json(),
            ),
        )


def _insert_head(
    connection: sqlite3.Connection,
    operation_id: str,
    entity_kind: str,
    expected: HeadExpectation,
) -> None:
    _ = connection.execute(
        """
        INSERT INTO operation_heads(
            operation_id,entity_kind,entity_id,expected_revision_id,resulting_revision_id
        ) VALUES (?,?,?,?,?)
        """,
        (
            operation_id,
            entity_kind,
            expected.entity_id,
            expected.expected_revision_id,
            expected.resulting_revision_id,
        ),
    )


def _published(
    published: dict[RevisionTarget, PublishedRevisionFile],
    target: RevisionTarget,
) -> PublishedRevisionFile:
    try:
        return published[target]
    except KeyError as error:
        conflict("prepared_revision_missing", repr(target)).with_traceback(error.__traceback__)


def _page_index(
    operation_id: str,
    workspace_id: str,
    page_write: PageRevisionWrite,
) -> IndexOutboxItem:
    return IndexOutboxItem(
        item_id=f"index.{operation_id}.{page_write.page.page_id}",
        workspace_id=workspace_id,
        entity_kind="page",
        entity_id=page_write.page.page_id,
        revision_id=page_write.revision.revision_id,
    )


def _memory_index(
    operation_id: str,
    workspace_id: str,
    memory_write: MemoryRevisionWrite,
) -> IndexOutboxItem:
    return IndexOutboxItem(
        item_id=f"index.{operation_id}.{memory_write.document.document_id}",
        workspace_id=workspace_id,
        entity_kind="memory",
        entity_id=memory_write.document.document_id,
        revision_id=memory_write.revision.revision_id,
    )


def _insert_redirects(
    connection: sqlite3.Connection,
    workspace_id: str,
    command: CatalogCommit,
) -> None:
    for redirect in command.redirects:
        if redirect.from_page_id == redirect.to_page_id:
            conflict("page_redirect_cycle", redirect.from_page_id)
        cycle = _OPTIONAL_STRING_ROW.validate_python(
            connection.execute(
                """
            WITH RECURSIVE chain(page_id) AS (
                SELECT ? UNION ALL
                SELECT redirect.to_page_id FROM page_redirects AS redirect
                JOIN chain ON redirect.from_page_id=chain.page_id
                WHERE redirect.workspace_id=?
            ) SELECT page_id FROM chain WHERE page_id=? LIMIT 1
            """,
                (redirect.to_page_id, workspace_id, redirect.from_page_id),
            ).fetchone(),
        )
        if cycle is not None:
            conflict("page_redirect_cycle", redirect.from_page_id)
        _ = connection.execute(
            """
            INSERT INTO page_redirects(workspace_id,from_page_id,to_page_id,operation_id)
            VALUES (?,?,?,?)
            """,
            (
                workspace_id,
                redirect.from_page_id,
                redirect.to_page_id,
                command.operation_id,
            ),
        )


__all__ = ["commit_catalog"]

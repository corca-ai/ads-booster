from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import (
    AccessScope,
    Brand,
    MemoryDocument,
    MemoryEntry,
    MemoryRevision,
    OperationReceipt,
    ScopeKind,
)
from ads_booster.knowledge.file_store import MemoryRevisionTarget, PublishedRevisionFile
from ads_booster.knowledge.grant_policy import authorize_read
from ads_booster.knowledge.repository_evidence import insert_memory_entry
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.repository_source import (
    _insert_operation,
    _operation_receipt,
    _operation_receipt_in,
    _require_read,
)
from ads_booster.knowledge.repository_types import (
    BrandRegistration,
    HeadExpectation,
    MemoryRevisionWrite,
    StoredMemory,
    conflict,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.contracts import ActorContext
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository

_STRING = TypeAdapter(str)
_INTEGER = TypeAdapter(int)


def assert_memory_head(
    connection: sqlite3.Connection,
    workspace_id: str,
    write: MemoryRevisionWrite,
) -> None:
    row = cast(
        "tuple[object, ...] | None",
        connection.execute(
            "SELECT revision_id FROM memory_heads WHERE workspace_id=? AND document_id=?",
            (workspace_id, write.document.document_id),
        ).fetchone(),
    )
    current = None if row is None else _STRING.validate_python(row[0])
    if current != write.expected.expected_revision_id:
        conflict("memory_head_conflict", write.document.document_id)
    if (
        write.expected.entity_id != write.document.document_id
        or write.expected.resulting_revision_id != write.revision.revision_id
        or write.document.head_revision_id != write.revision.revision_id
        or write.revision.document_id != write.document.document_id
        or write.revision.previous_revision_id != write.expected.expected_revision_id
        or set(write.revision.entry_ids) != {item.entry_id for item in write.entries}
    ):
        conflict("memory_write_binding_conflict", write.document.document_id)


def insert_memory_shell(
    connection: sqlite3.Connection,
    write: MemoryRevisionWrite,
) -> None:
    if write.expected.expected_revision_id is not None:
        return
    document = write.document
    shared_scope = AccessScope(kind=ScopeKind.WORKSPACE, workspace_id=document.workspace_id)
    _ = connection.execute(
        """
        INSERT INTO memory_documents(
            workspace_id,document_id,kind,brand_id,local_date,timezone,
            scope_key,document_json
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            document.workspace_id,
            document.document_id,
            document.kind.value,
            document.brand_id,
            None if document.local_date is None else document.local_date.isoformat(),
            document.timezone,
            scope_key(shared_scope),
            document.model_dump_json(),
        ),
    )


def insert_memory_revision(
    connection: sqlite3.Connection,
    operation_id: str,
    write: MemoryRevisionWrite,
    published: PublishedRevisionFile,
) -> None:
    document = write.document
    expected_target = MemoryRevisionTarget(
        workspace_id=document.workspace_id,
        document_id=document.document_id,
        revision_id=write.revision.revision_id,
    )
    if published.target != expected_target or published.sha256 != write.revision.body_sha256:
        conflict("memory_file_binding_conflict", document.document_id)
    _ = connection.execute(
        """
        INSERT INTO memory_revisions(
            workspace_id,document_id,revision_id,previous_revision_id,body_sha256,
            relative_path,revision_json,operation_id,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            document.workspace_id,
            document.document_id,
            write.revision.revision_id,
            write.revision.previous_revision_id,
            write.revision.body_sha256,
            published.relative_path,
            write.revision.model_dump_json(),
            operation_id,
            write.revision.created_at.isoformat(),
        ),
    )
    for entry in write.entries:
        insert_memory_entry(connection, document.workspace_id, write.revision.revision_id, entry)
    for binding in write.constraints:
        if binding.entry_id not in write.revision.entry_ids:
            conflict("constraint_entry_missing", binding.constraint_id)
        _ = connection.execute(
            """
            INSERT INTO constraint_bindings(
                workspace_id,constraint_id,document_id,memory_revision_id,entry_id,
                authority_class,subject_key,compatibility,binding_json
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                document.workspace_id,
                binding.constraint_id,
                document.document_id,
                write.revision.revision_id,
                binding.entry_id,
                binding.authority_class.value,
                binding.applies_to.subject_key,
                binding.compatibility.value,
                binding.model_dump_json(),
            ),
        )
    previous = cast(
        "tuple[object, ...] | None",
        connection.execute(
            "SELECT head_sequence FROM memory_heads WHERE workspace_id=? AND document_id=?",
            (document.workspace_id, document.document_id),
        ).fetchone(),
    )
    sequence = 1 if previous is None else _INTEGER.validate_python(previous[0]) + 1
    _ = connection.execute(
        """
        INSERT INTO memory_heads(workspace_id,document_id,revision_id,head_sequence,operation_id)
        VALUES (?,?,?,?,?)
        ON CONFLICT(workspace_id,document_id) DO UPDATE SET
            revision_id=excluded.revision_id,
            head_sequence=excluded.head_sequence,
            operation_id=excluded.operation_id
        """,
        (
            document.workspace_id,
            document.document_id,
            write.revision.revision_id,
            sequence,
            operation_id,
        ),
    )
    _ = connection.execute(
        """
        UPDATE memory_documents SET document_json=?
        WHERE workspace_id=? AND document_id=?
        """,
        (document.model_dump_json(), document.workspace_id, document.document_id),
    )
    _insert_memory_view(connection, operation_id, write)


def _insert_memory_view(
    connection: sqlite3.Connection,
    operation_id: str,
    write: MemoryRevisionWrite,
) -> None:
    document = write.document
    item_id = f"view.{operation_id}.{document.document_id}"
    unique_key = f"{document.document_id}:{write.revision.revision_id}:{document.kind.value}"
    _ = connection.execute(
        """
        INSERT INTO memory_view_outbox(
            item_id,workspace_id,document_id,revision_id,view_kind,unique_key,state
        ) VALUES (?,?,?,?,?,?,'pending')
        """,
        (
            item_id,
            document.workspace_id,
            document.document_id,
            write.revision.revision_id,
            document.kind.value,
            unique_key,
        ),
    )


def register_brand(
    repository: KnowledgeRepository,
    command: BrandRegistration,
) -> OperationReceipt:
    existing = _operation_receipt(repository, command.receipt.operation_id, command.payload_sha256)
    if existing is not None:
        return OperationReceipt.model_validate_json(existing)
    published = repository.files.publish(command.prepared_file)
    write = MemoryRevisionWrite(
        document=command.document,
        revision=command.revision,
        expected=HeadExpectation(
            entity_id=command.document.document_id,
            expected_revision_id=None,
            resulting_revision_id=command.revision.revision_id,
        ),
        prepared_file=command.prepared_file,
    )
    try:
        with repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            replay = _operation_receipt_in(
                connection,
                command.receipt.operation_id,
                command.payload_sha256,
            )
            if replay is not None:
                return OperationReceipt.model_validate_json(replay)
            _ = connection.execute(
                """
                INSERT INTO brands(workspace_id,brand_id,name,revision,state,brand_json)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    command.brand.workspace_id,
                    command.brand.brand_id,
                    command.brand.name,
                    command.brand.revision,
                    command.brand.state.value,
                    command.brand.model_dump_json(),
                ),
            )
            _ = connection.execute(
                """
                INSERT INTO brand_events(
                    workspace_id,event_id,brand_id,kind,expected_revision,event_json,occurred_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    command.event.workspace_id,
                    command.event.event_id,
                    command.event.brand_id,
                    command.event.kind.value,
                    command.event.expected_revision,
                    command.event.model_dump_json(),
                    command.event.occurred_at.isoformat(),
                ),
            )
            assert_memory_head(connection, command.brand.workspace_id, write)
            insert_memory_shell(connection, write)
            _insert_operation(
                connection,
                (
                    command.receipt.operation_id,
                    command.brand.workspace_id,
                    "brand_register",
                    command.payload_sha256,
                    command.receipt.model_dump_json(),
                    command.receipt.occurred_at.isoformat(),
                ),
            )
            insert_memory_revision(connection, command.receipt.operation_id, write, published)
            _ = connection.execute(
                """
                INSERT INTO operation_heads(
                    operation_id,entity_kind,entity_id,expected_revision_id,resulting_revision_id
                ) VALUES (?,'brand',?,NULL,?),(?,'memory',?,NULL,?)
                """,
                (
                    command.receipt.operation_id,
                    command.brand.brand_id,
                    str(command.brand.revision),
                    command.receipt.operation_id,
                    command.document.document_id,
                    command.revision.revision_id,
                ),
            )
    except sqlite3.IntegrityError as error:
        conflict("brand_registration_conflict", command.brand.brand_id).with_traceback(
            error.__traceback__
        )
    return command.receipt


def brand(
    repository: KnowledgeRepository,
    actor: ActorContext,
    brand_id: str,
) -> Brand | None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                "SELECT brand_json FROM brands WHERE workspace_id=? AND brand_id=?",
                (actor.workspace_id, brand_id),
            ).fetchone(),
        )
    return None if row is None else Brand.model_validate_json(_STRING.validate_python(row[0]))


def read_memory(
    repository: KnowledgeRepository,
    actor: ActorContext,
    document_id: str,
    revision_id: str | None,
) -> StoredMemory | None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        selected = revision_id
        if selected is None:
            head = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    "SELECT revision_id FROM memory_heads WHERE workspace_id=? AND document_id=?",
                    (actor.workspace_id, document_id),
                ).fetchone(),
            )
            if head is None:
                return None
            selected = _STRING.validate_python(head[0])
        redacted = connection.execute(
            """
            SELECT 1 FROM history_redactions
            WHERE workspace_id=? AND entity_kind='memory_document'
                AND entity_id=? AND revision_id=?
            """,
            (actor.workspace_id, document_id, selected),
        ).fetchone()
        if redacted is not None:
            return None
        blocked_entry = connection.execute(
            """
            SELECT 1 FROM memory_entries AS entry
            JOIN tombstones AS tomb ON tomb.workspace_id=entry.workspace_id
                AND tomb.target_kind='memory_entry' AND tomb.target_id=entry.entry_id
                AND (tomb.target_revision_id='' OR tomb.target_revision_id=entry.memory_revision_id)
            WHERE entry.workspace_id=? AND entry.document_id=?
                AND entry.memory_revision_id=?
                AND tomb.state IN ('blocked','purge_pending','purged') LIMIT 1
            """,
            (actor.workspace_id, document_id, selected),
        ).fetchone()
        if blocked_entry is not None:
            return None
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
                SELECT document.document_json,revision.revision_json,revision.body_sha256,
                    scope.scope_json
                FROM memory_documents AS document
                JOIN memory_revisions AS revision USING(workspace_id,document_id)
                JOIN access_scopes AS scope ON scope.scope_key=document.scope_key
                WHERE document.workspace_id=? AND document.document_id=? AND revision.revision_id=?
                AND NOT EXISTS (
                    SELECT 1 FROM tombstones AS tomb
                    WHERE tomb.workspace_id=document.workspace_id
                    AND tomb.target_id=document.document_id
                    AND tomb.state IN ('blocked','purge_pending','purged')
                )
                """,
                (actor.workspace_id, document_id, selected),
            ).fetchone(),
        )
        if row is None:
            return None
        shared_scope = AccessScope.model_validate_json(_STRING.validate_python(row[3]))
        _ = authorize_read(actor=actor, target_scope=shared_scope, at=datetime.now(UTC))
        entry_rows = cast(
            "list[tuple[object, ...]]",
            connection.execute(
                """
                SELECT entry_json FROM memory_entries
                WHERE workspace_id=? AND document_id=? AND memory_revision_id=?
                AND NOT EXISTS (
                    SELECT 1 FROM tombstones AS tomb
                    WHERE tomb.workspace_id=memory_entries.workspace_id
                    AND tomb.target_kind='memory_entry'
                    AND tomb.target_id=memory_entries.entry_id
                    AND (tomb.target_revision_id=''
                        OR tomb.target_revision_id=memory_entries.memory_revision_id)
                    AND tomb.state IN ('blocked','purge_pending','purged')
                ) ORDER BY entry_id
                """,
                (actor.workspace_id, document_id, selected),
            ).fetchall(),
        )
    document = MemoryDocument.model_validate_json(_STRING.validate_python(row[0]))
    revision = MemoryRevision.model_validate_json(_STRING.validate_python(row[1]))
    entries = tuple(
        MemoryEntry.model_validate_json(_STRING.validate_python(item[0])) for item in entry_rows
    )
    target = MemoryRevisionTarget(
        workspace_id=actor.workspace_id,
        document_id=document_id,
        revision_id=revision.revision_id,
    )
    published = repository.files.published(target, _STRING.validate_python(row[2]))
    return StoredMemory(
        document=document,
        revision=revision,
        entries=entries,
        body=repository.files.read(published),
    )


__all__ = [
    "assert_memory_head",
    "brand",
    "insert_memory_revision",
    "insert_memory_shell",
    "read_memory",
    "register_brand",
]

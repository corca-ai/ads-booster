from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from ads_booster.knowledge.contracts import (
    IngestReceipt,
    KnowledgeJob,
    Source,
    SourceDisposition,
    SourceKind,
    SourceSegment,
)
from ads_booster.knowledge.file_store import (
    PublishedRevisionFile,
    SourceFileKind,
    SourceRevisionTarget,
)
from ads_booster.knowledge.grant_policy import (
    authorize_read,
    authorize_write,
    require_current_policy_epoch,
)
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.repository_types import (
    IndexOutboxItem,
    SourceAdmissionChange,
    SourceObservationWrite,
    SourceRegistration,
    StoredSource,
    conflict,
)

if TYPE_CHECKING:
    from ads_booster.knowledge.contracts import ActorContext
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository

_STRING = TypeAdapter(str)
_INTEGER = TypeAdapter(int)
type OperationRow = tuple[str, str, str, str, str, str]


def register_source(
    repository: KnowledgeRepository,
    command: SourceRegistration,
) -> IngestReceipt:
    existing = _operation_receipt(repository, command.operation_id, command.payload_sha256)
    if existing is not None:
        return IngestReceipt.model_validate_json(existing).model_copy(update={"replayed": True})
    published = tuple(repository.files.publish(item) for item in command.prepared_files)
    try:
        with repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            replay = _operation_receipt_in(
                connection,
                command.operation_id,
                command.payload_sha256,
            )
            if replay is not None:
                return IngestReceipt.model_validate_json(replay).model_copy(
                    update={"replayed": True}
                )
            _insert_source(connection, command, published)
            _insert_conversation_event(connection, command)
            if command.observation is not None:
                _insert_source_observation(connection, command.observation)
            _insert_job(connection, command.job.job, command.job.unique_key)
            _insert_index(connection, command.index_item)
            receipt_json = command.receipt.model_dump_json()
            _insert_operation(
                connection,
                (
                    command.operation_id,
                    command.source.workspace_id,
                    "source_register",
                    command.payload_sha256,
                    receipt_json,
                    command.source.fetched_at.isoformat(),
                ),
            )
            _ = connection.execute(
                """
                INSERT INTO delivery_receipts(
                    workspace_id,delivery_id,payload_sha256,source_id,source_revision_id,
                    operation_id,job_id,index_item_id,receipt_json
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    command.source.workspace_id,
                    command.receipt.delivery_id,
                    command.payload_sha256,
                    command.source.source_id,
                    command.source.revision_id,
                    command.operation_id,
                    command.job.job.job_id,
                    command.index_item.item_id,
                    receipt_json,
                ),
            )
    except sqlite3.IntegrityError as error:
        conflict("source_registration_conflict", command.source.source_id).with_traceback(
            error.__traceback__
        )
    return command.receipt


def _insert_conversation_event(
    connection: sqlite3.Connection,
    command: SourceRegistration,
) -> None:
    event = command.conversation_event
    if event is None:
        return
    if event.scope.workspace_id != command.source.workspace_id:
        conflict("conversation_event_workspace_conflict", event.message_id)
    previous = cast(
        "tuple[object, ...] | None",
        connection.execute(
            """
            SELECT revision,sequence FROM conversation_events
            WHERE workspace_id=? AND conversation_id=? AND message_id=?
            ORDER BY revision DESC LIMIT 1
            """,
            (command.source.workspace_id, event.conversation_id, event.message_id),
        ).fetchone(),
    )
    if previous is not None and (
        event.revision != _INTEGER.validate_python(previous[0]) + 1
        or event.sequence < _INTEGER.validate_python(previous[1])
    ):
        conflict("conversation_event_revision_conflict", event.message_id)
    _ = connection.execute(
        """
        INSERT INTO conversation_events(
            workspace_id,conversation_id,message_id,revision,event_kind,sequence,
            scope_key,speaker_ref,event_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            command.source.workspace_id,
            event.conversation_id,
            event.message_id,
            event.revision,
            event.event_kind.value,
            event.sequence,
            scope_key(event.scope),
            event.speaker_ref,
            event.model_dump_json(),
            event.created_at.isoformat(),
        ),
    )


def _insert_source(
    connection: sqlite3.Connection,
    command: SourceRegistration,
    published: tuple[PublishedRevisionFile, ...],
) -> None:
    source = command.source
    visibility = (
        "blocked"
        if command.conversation_event is not None
        and command.conversation_event.event_kind.value == "message_deleted"
        else "hidden"
    )
    current = cast(
        "tuple[object, ...] | None",
        connection.execute(
            """
            SELECT revision_id,revision_number FROM source_revisions
            WHERE workspace_id=? AND source_id=? ORDER BY revision_number DESC LIMIT 1
            """,
            (source.workspace_id, source.source_id),
        ).fetchone(),
    )
    previous_revision_id: str | None = None
    if current is None:
        if source.revision != 1:
            conflict("source_revision_sequence_conflict", source.source_id)
        _ = connection.execute(
            """
            INSERT INTO sources(
                workspace_id,source_id,scope_key,owner_ref,source_kind,source_identity,
                sanitized_locator,disposition,admission_revision,visibility,source_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                source.workspace_id,
                source.source_id,
                scope_key(source.scope),
                source.owner_ref,
                source.source_kind.value,
                source.source_identity,
                source.sanitized_locator,
                source.disposition.value,
                source.admission_revision,
                visibility,
                source.model_dump_json(),
            ),
        )
    else:
        previous_revision_id = _STRING.validate_python(current[0])
        previous_number = _INTEGER.validate_python(current[1])
        if source.revision != previous_number + 1:
            conflict("source_revision_sequence_conflict", source.source_id)
        _ = connection.execute(
            """
            UPDATE sources SET disposition=?,admission_revision=?,visibility=?,source_json=?
            WHERE workspace_id=? AND source_id=?
            """,
            (
                source.disposition.value,
                source.admission_revision,
                visibility,
                source.model_dump_json(),
                source.workspace_id,
                source.source_id,
            ),
        )
    _ = connection.execute(
        """
        INSERT INTO source_revisions(
            workspace_id,source_id,revision_id,revision_number,previous_revision_id,sha256,
            mime_type,byte_length,extraction_status,completeness,extractor_version,fetched_at,
            origin_created_at,error_code,revision_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            source.workspace_id,
            source.source_id,
            source.revision_id,
            source.revision,
            previous_revision_id,
            source.sha256,
            source.mime_type,
            source.byte_length,
            source.extraction_status.value,
            source.completeness.value,
            source.extractor_version,
            source.fetched_at.isoformat(),
            None if source.origin_created_at is None else source.origin_created_at.isoformat(),
            source.error_code,
            source.model_dump_json(),
        ),
    )
    _ = connection.execute(
        """
        INSERT INTO source_heads(workspace_id,source_id,revision_id) VALUES (?,?,?)
        ON CONFLICT(workspace_id,source_id) DO UPDATE SET revision_id=excluded.revision_id
        """,
        (source.workspace_id, source.source_id, source.revision_id),
    )
    for item in published:
        if not isinstance(item.target, SourceRevisionTarget):
            conflict("source_file_target_invalid", source.source_id)
        _ = connection.execute(
            """
            INSERT INTO source_files(
                workspace_id,source_id,revision_id,file_kind,relative_path,sha256,byte_length
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                source.workspace_id,
                source.source_id,
                source.revision_id,
                item.target.file_kind.value,
                item.relative_path,
                item.sha256,
                item.byte_length,
            ),
        )
    for segment in command.segments:
        if segment.source_id != source.source_id or segment.revision_id != source.revision_id:
            conflict("source_segment_revision_conflict", segment.segment_id)
        _ = connection.execute(
            """
            INSERT INTO segments(
                workspace_id,source_id,revision_id,extraction_version,segment_id,
                content_sha256,locator_json,quote_start,quote_end,completeness,segment_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                source.workspace_id,
                segment.source_id,
                segment.revision_id,
                segment.extraction_version,
                segment.segment_id,
                segment.content_sha256,
                segment.locator.model_dump_json(),
                segment.quote_range.start,
                segment.quote_range.end,
                segment.completeness.value,
                segment.model_dump_json(),
            ),
        )


def change_source_admission(
    repository: KnowledgeRepository,
    command: SourceAdmissionChange,
) -> Source:
    searchable = command.disposition in {
        SourceDisposition.REFERENCE,
        SourceDisposition.ADMIT,
        SourceDisposition.UPDATE,
    }
    try:
        with repository.connection() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            existing = _operation_receipt_in(
                connection,
                command.operation_id,
                command.payload_sha256,
            )
            if existing is not None:
                return Source.model_validate_json(existing)
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    "SELECT source_json FROM sources WHERE workspace_id=? AND source_id=?",
                    (command.workspace_id, command.source_id),
                ).fetchone(),
            )
            if row is None:
                conflict("source_not_found", command.source_id)
            current = Source.model_validate_json(_STRING.validate_python(row[0]))
            updated = current.model_copy(
                update={
                    "disposition": command.disposition,
                    "admission_revision": command.expected_admission_revision + 1,
                }
            )
            cursor = connection.execute(
                """
                UPDATE sources SET disposition=?,admission_revision=?,visibility=?,source_json=?
                WHERE workspace_id=? AND source_id=? AND admission_revision=?
                """,
                (
                    updated.disposition.value,
                    updated.admission_revision,
                    "searchable" if searchable else "hidden",
                    updated.model_dump_json(),
                    command.workspace_id,
                    command.source_id,
                    command.expected_admission_revision,
                ),
            )
            if cursor.rowcount != 1:
                conflict("source_admission_revision_conflict", command.source_id)
            if command.index_item.admission_revision != updated.admission_revision:
                conflict("source_admission_index_conflict", command.source_id)
            _insert_index(connection, command.index_item)
            _insert_operation(
                connection,
                (
                    command.operation_id,
                    command.workspace_id,
                    "source_admission",
                    command.payload_sha256,
                    updated.model_dump_json(),
                    command.occurred_at.isoformat(),
                ),
            )
            return updated
    except sqlite3.IntegrityError as error:
        conflict("source_admission_conflict", command.source_id).with_traceback(error.__traceback__)


def read_source(
    repository: KnowledgeRepository,
    actor: ActorContext,
    source_id: str,
) -> StoredSource | None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
                SELECT source_json FROM sources
                WHERE workspace_id=? AND source_id=? AND visibility!='blocked'
                """,
                (actor.workspace_id, source_id),
            ).fetchone(),
        )
        if row is None:
            return None
        source = Source.model_validate_json(_STRING.validate_python(row[0]))
        _ = authorize_read(actor=actor, target_scope=source.scope, at=datetime.now(UTC))
        segment_rows = cast(
            "list[tuple[object, ...]]",
            connection.execute(
                """
                SELECT segment_json FROM segments
                WHERE workspace_id=? AND source_id=? AND revision_id=?
                ORDER BY extraction_version,segment_id
                """,
                (actor.workspace_id, source_id, source.revision_id),
            ).fetchall(),
        )
        segments = tuple(
            SourceSegment.model_validate_json(_STRING.validate_python(item[0]))
            for item in segment_rows
        )
        file_row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
                SELECT file_kind,sha256 FROM source_files
                WHERE workspace_id=? AND source_id=? AND revision_id=?
                ORDER BY CASE file_kind WHEN 'original.bin' THEN 0 ELSE 1 END LIMIT 1
                """,
                (actor.workspace_id, source_id, source.revision_id),
            ).fetchone(),
        )
    if file_row is None:
        conflict("source_file_missing", source_id)
    target = SourceRevisionTarget(
        source_id=source_id,
        revision_id=source.revision_id,
        file_kind=SourceFileKind(_STRING.validate_python(file_row[0])),
    )
    published = repository.files.published(target, _STRING.validate_python(file_row[1]))
    return StoredSource(source=source, segments=segments, body=repository.files.read(published))


def source_by_identity(
    repository: KnowledgeRepository,
    actor: ActorContext,
    source_kind: SourceKind,
    source_identity: str,
) -> Source | None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
            SELECT source_json FROM sources
            WHERE workspace_id=? AND scope_key=? AND source_kind=? AND source_identity=?
            AND visibility!='blocked'
            """,
                (
                    actor.workspace_id,
                    scope_key(actor.conversation_scope),
                    source_kind.value,
                    source_identity,
                ),
            ).fetchone(),
        )
    if row is None:
        return None
    source = Source.model_validate_json(_STRING.validate_python(row[0]))
    _ = authorize_read(actor=actor, target_scope=source.scope, at=datetime.now(UTC))
    return source


def source_ingest_head(
    repository: KnowledgeRepository,
    actor: ActorContext,
    source_kind: SourceKind,
    source_identity: str,
) -> Source | None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
            SELECT source_json FROM sources
            WHERE workspace_id=? AND scope_key=? AND source_kind=? AND source_identity=?
            AND visibility!='blocked'
            """,
                (
                    actor.workspace_id,
                    scope_key(actor.conversation_scope),
                    source_kind.value,
                    source_identity,
                ),
            ).fetchone(),
        )
    if row is None:
        return None
    source = Source.model_validate_json(_STRING.validate_python(row[0]))
    _ = authorize_write(actor=actor, target_scope=source.scope, at=datetime.now(UTC))
    return source


def latest_source_observation(
    repository: KnowledgeRepository,
    actor: ActorContext,
    source_id: str,
) -> SourceObservationWrite | None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
            SELECT source.source_json,observation.observation_id,observation.revision_id,
                observation.observed_at,observation.final_url,observation.http_status,
                observation.etag,observation.last_modified
            FROM sources AS source
            JOIN source_observations AS observation USING(workspace_id,source_id)
            WHERE source.workspace_id=? AND source.source_id=?
            ORDER BY observation.observed_at DESC,observation.observation_id DESC LIMIT 1
            """,
                (actor.workspace_id, source_id),
            ).fetchone(),
        )
    if row is None:
        return None
    source = Source.model_validate_json(_STRING.validate_python(row[0]))
    _ = authorize_write(actor=actor, target_scope=source.scope, at=datetime.now(UTC))
    return SourceObservationWrite(
        observation_id=_STRING.validate_python(row[1]),
        workspace_id=actor.workspace_id,
        source_id=source_id,
        revision_id=_STRING.validate_python(row[2]),
        fetched_at=datetime.fromisoformat(_STRING.validate_python(row[3])),
        final_url=_STRING.validate_python(row[4]),
        http_status=_INTEGER.validate_python(row[5]),
        etag=None if row[6] is None else _STRING.validate_python(row[6]),
        last_modified=None if row[7] is None else _STRING.validate_python(row[7]),
    )


def record_source_observation(
    repository: KnowledgeRepository,
    actor: ActorContext,
    observation: SourceObservationWrite,
) -> bool:
    digest_input = (
        f"{observation.workspace_id}\x1f{observation.source_id}\x1f{observation.revision_id}\x1f"
        f"{observation.fetched_at.isoformat()}\x1f{observation.final_url}\x1f"
        f"{observation.http_status}\x1f{observation.etag or ''}\x1f"
        f"{observation.last_modified or ''}"
    )
    metadata_sha256 = sha256(digest_input.encode()).hexdigest()
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        _require_read(connection, actor)
        if actor.workspace_id != observation.workspace_id:
            conflict("source_observation_workspace_conflict", observation.observation_id)
        source_row = cast(
            "tuple[object, ...] | None",
            connection.execute(
                "SELECT source_json FROM sources WHERE workspace_id=? AND source_id=?",
                (actor.workspace_id, observation.source_id),
            ).fetchone(),
        )
        if source_row is None:
            conflict("source_not_found", observation.source_id)
        source = Source.model_validate_json(_STRING.validate_python(source_row[0]))
        _ = authorize_write(actor=actor, target_scope=source.scope, at=datetime.now(UTC))
        existing = cast(
            "tuple[object, ...] | None",
            connection.execute(
                """
            SELECT metadata_sha256 FROM source_observations
            WHERE workspace_id=? AND observation_id=?
            """,
                (observation.workspace_id, observation.observation_id),
            ).fetchone(),
        )
        if existing is not None:
            if _STRING.validate_python(existing[0]) == metadata_sha256:
                return False
            conflict("source_observation_idempotency_conflict", observation.observation_id)
        _insert_source_observation(connection, observation, metadata_sha256)
    return True


def _insert_source_observation(
    connection: sqlite3.Connection,
    observation: SourceObservationWrite,
    metadata_sha256: str | None = None,
) -> None:
    if metadata_sha256 is None:
        digest_input = (
            f"{observation.workspace_id}\x1f{observation.source_id}\x1f"
            f"{observation.revision_id}\x1f{observation.fetched_at.isoformat()}\x1f"
            f"{observation.final_url}\x1f{observation.http_status}\x1f"
            f"{observation.etag or ''}\x1f{observation.last_modified or ''}"
        )
        metadata_sha256 = sha256(digest_input.encode()).hexdigest()
    _ = connection.execute(
        """
        INSERT INTO source_observations(
            workspace_id,observation_id,source_id,revision_id,delivery_id,observed_at,
            final_url,http_status,etag,last_modified,metadata_sha256
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            observation.workspace_id,
            observation.observation_id,
            observation.source_id,
            observation.revision_id,
            observation.observation_id,
            observation.fetched_at.isoformat(),
            observation.final_url,
            observation.http_status,
            observation.etag,
            observation.last_modified,
            metadata_sha256,
        ),
    )


def _require_read(connection: sqlite3.Connection, actor: ActorContext) -> None:
    row = cast(
        "tuple[object, ...] | None",
        connection.execute(
            """
            SELECT policy_epoch FROM workspaces WHERE workspace_id=? AND state='active'
            """,
            (actor.workspace_id,),
        ).fetchone(),
    )
    if row is None:
        conflict("workspace_not_found", actor.workspace_id)
    require_current_policy_epoch(actor=actor, current_epoch=_INTEGER.validate_python(row[0]))


def _insert_job(connection: sqlite3.Connection, job: KnowledgeJob, unique_key: str) -> None:
    _ = connection.execute(
        """
        INSERT INTO jobs(
            job_id,workspace_id,scope_key,kind,state,priority,unique_key,batch_id,
            root_event_id,policy_version,due_at,created_at,reason_code,lease_generation,job_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            job.job_id,
            job.workspace_id,
            scope_key(job.scope),
            job.kind.value,
            job.state.value,
            job.priority.value,
            unique_key,
            job.batch_id,
            job.root_event_id,
            job.policy_version,
            job.due_at.isoformat(),
            job.created_at.isoformat(),
            job.reason_code,
            job.lease_generation,
            job.model_dump_json(),
        ),
    )


def _insert_index(connection: sqlite3.Connection, item: IndexOutboxItem) -> None:
    unique_key = ":".join(
        (
            item.entity_kind,
            item.entity_id,
            item.revision_id,
            item.extraction_version or "",
            "" if item.admission_revision is None else str(item.admission_revision),
            item.indexer_version,
        )
    )
    _ = connection.execute(
        """
        INSERT INTO index_outbox(
            item_id,workspace_id,entity_kind,entity_id,revision_id,extraction_version,
            admission_revision,indexer_version,unique_key,state
        ) VALUES (?,?,?,?,?,?,?,?,?,'pending')
        """,
        (
            item.item_id,
            item.workspace_id,
            item.entity_kind,
            item.entity_id,
            item.revision_id,
            item.extraction_version,
            item.admission_revision,
            item.indexer_version,
            unique_key,
        ),
    )


def _insert_operation(
    connection: sqlite3.Connection,
    operation: OperationRow,
) -> None:
    operation_id, workspace_id, operation_kind, payload_sha256, receipt_json, occurred_at = (
        operation
    )
    _ = connection.execute(
        """
        INSERT INTO operations(
            operation_id,workspace_id,operation_kind,payload_sha256,status,
            receipt_sha256,receipt_json,created_at,committed_at
        ) VALUES (?,?,?,?,'applied',?,?,?,?)
        """,
        (
            operation_id,
            workspace_id,
            operation_kind,
            payload_sha256,
            sha256(receipt_json.encode()).hexdigest(),
            receipt_json,
            occurred_at,
            occurred_at,
        ),
    )


def _operation_receipt(
    repository: KnowledgeRepository,
    operation_id: str,
    payload_sha256: str,
) -> str | None:
    with repository.connection() as connection:
        return _operation_receipt_in(connection, operation_id, payload_sha256)


def _operation_receipt_in(
    connection: sqlite3.Connection,
    operation_id: str,
    payload_sha256: str,
) -> str | None:
    row = cast(
        "tuple[object, ...] | None",
        connection.execute(
            "SELECT payload_sha256,receipt_json FROM operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone(),
    )
    if row is None:
        return None
    if _STRING.validate_python(row[0]) != payload_sha256:
        conflict("operation_idempotency_conflict", operation_id)
    return _STRING.validate_python(row[1])


__all__ = [
    "_insert_index",
    "_insert_job",
    "_insert_operation",
    "_operation_receipt",
    "_operation_receipt_in",
    "_require_read",
    "change_source_admission",
    "latest_source_observation",
    "read_source",
    "record_source_observation",
    "register_source",
    "source_by_identity",
    "source_ingest_head",
]

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.knowledge.file_paths import RevisionFileDraft, SkillRevisionTarget
from ads_booster.knowledge.repository_source import _require_read
from ads_booster.knowledge.repository_types import (
    HeadExpectation,
    SkillRevisionWrite,
    StoredSkill,
    conflict,
)
from ads_booster.knowledge.skill_contracts import SkillOperation, SkillRecord

if TYPE_CHECKING:
    import sqlite3

    from ads_booster.knowledge.file_store import PublishedRevisionFile
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository
    from ads_booster.knowledge.scope_contracts import ActorContext

_SKILL_ROW: TypeAdapter[tuple[str, str, str, int] | None] = TypeAdapter(
    tuple[str, str, str, int] | None
)
_SKILL_ID_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_HEAD_ROW: TypeAdapter[tuple[str, int] | None] = TypeAdapter(tuple[str, int] | None)


def prepare_skill_write(
    repository: KnowledgeRepository,
    workspace_id: str,
    operation: SkillOperation,
    body: bytes | None,
) -> SkillRevisionWrite:
    record = operation.record
    prepared = None
    if record is not None:
        if body is None:
            conflict("skill_revision_body_missing", operation.skill_id)
        prepared = repository.files.prepare(
            RevisionFileDraft(
                operation_id=operation.operation_id,
                target=SkillRevisionTarget(
                    workspace_id=workspace_id,
                    skill_id=operation.skill_id,
                    revision_id=record.version,
                ),
                content=body,
                sha256=sha256(body).hexdigest(),
            )
        )
    return SkillRevisionWrite(
        operation=operation,
        expected=HeadExpectation(
            entity_id=operation.skill_id,
            expected_revision_id=operation.expected_revision_id,
            resulting_revision_id=operation.replacement_revision_id or "none",
        ),
        record=record,
        prepared_file=prepared,
    )


def read_skill(
    repository: KnowledgeRepository,
    actor: ActorContext,
    skill_id: str,
    revision_id: str | None = None,
) -> StoredSkill | None:
    with repository.connection() as connection:
        _require_read(connection, actor)
        row = _SKILL_ROW.validate_python(
            connection.execute(
                """
                SELECT revision.record_json,revision.relative_path,revision.body_sha256,
                    skill.display_revision_id IS NOT revision.revision_id
                FROM skill_revisions AS revision
                JOIN skills AS skill USING(workspace_id,skill_id)
                WHERE revision.workspace_id=? AND revision.skill_id=?
                    AND revision.revision_id=COALESCE(
                    ?,(SELECT head.revision_id FROM skill_heads AS head
                       WHERE head.workspace_id=revision.workspace_id
                         AND head.skill_id=revision.skill_id)
                )
                """,
                (actor.workspace_id, skill_id, revision_id),
            ).fetchone()
        )
    if row is None:
        return None
    record = SkillRecord.model_validate_json(row[0])
    published = repository.files.published(
        SkillRevisionTarget(actor.workspace_id, skill_id, record.version),
        row[2],
    )
    if published.relative_path != row[1]:
        conflict("skill_revision_path_mismatch", skill_id)
    return StoredSkill(
        record=record, body=repository.files.read(published), display_pending=bool(row[3])
    )


def skill_ids(repository: KnowledgeRepository, actor: ActorContext) -> tuple[str, ...]:
    with repository.connection() as connection:
        _require_read(connection, actor)
        rows = _SKILL_ID_ROWS.validate_python(
            connection.execute(
                """
                SELECT head.skill_id FROM skill_heads AS head
                WHERE head.workspace_id=? ORDER BY head.skill_id
                """,
                (actor.workspace_id,),
            ).fetchall()
        )
    return tuple(row[0] for row in rows)


def assert_skill_head(
    connection: sqlite3.Connection,
    workspace_id: str,
    write: SkillRevisionWrite,
) -> None:
    row = _HEAD_ROW.validate_python(
        connection.execute(
            """
            SELECT revision_id,head_sequence FROM skill_heads
            WHERE workspace_id=? AND skill_id=?
            """,
            (workspace_id, write.operation.skill_id),
        ).fetchone()
    )
    actual = None if row is None else row[0]
    if actual != write.expected.expected_revision_id:
        conflict("skill_head_conflict", write.operation.skill_id)


def insert_skill_revision(
    connection: sqlite3.Connection,
    workspace_id: str,
    operation_id: str,
    write: SkillRevisionWrite,
    published: PublishedRevisionFile | None,
) -> None:
    record = write.record
    row = _HEAD_ROW.validate_python(
        connection.execute(
            "SELECT revision_id,head_sequence FROM skill_heads WHERE workspace_id=? AND skill_id=?",
            (workspace_id, write.operation.skill_id),
        ).fetchone()
    )
    if record is None:
        _ = connection.execute(
            "DELETE FROM skill_heads WHERE workspace_id=? AND skill_id=?",
            (workspace_id, write.operation.skill_id),
        )
        return
    if published is None:
        conflict("skill_revision_file_missing", write.operation.skill_id)
    _ = connection.execute(
        """
        INSERT INTO skills(workspace_id,skill_id,origin,protected,display_revision_id,created_at)
        VALUES (?,?,?,?,NULL,?)
        ON CONFLICT(workspace_id,skill_id) DO UPDATE SET
            origin=excluded.origin,protected=excluded.protected,display_revision_id=NULL
        """,
        (
            workspace_id,
            record.skill_id,
            record.origin.value,
            int(record.protected),
            record.created_at.isoformat(),
        ),
    )
    _ = connection.execute(
        """
        INSERT INTO skill_revisions(
            workspace_id,skill_id,revision_id,previous_revision_id,digest,body_sha256,
            relative_path,record_json,operation_id,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            workspace_id,
            record.skill_id,
            record.version,
            write.expected.expected_revision_id,
            record.digest,
            published.sha256,
            published.relative_path,
            record.model_dump_json(),
            operation_id,
            record.updated_at.isoformat(),
        ),
    )
    _ = connection.execute(
        """
        INSERT INTO skill_heads(workspace_id,skill_id,revision_id,head_sequence,operation_id)
        VALUES (?,?,?,?,?)
        ON CONFLICT(workspace_id,skill_id) DO UPDATE SET
            revision_id=excluded.revision_id,
            head_sequence=skill_heads.head_sequence+1,
            operation_id=excluded.operation_id
        """,
        (
            workspace_id,
            record.skill_id,
            record.version,
            1 if row is None else row[1] + 1,
            operation_id,
        ),
    )


def mark_skill_display_current(
    repository: KnowledgeRepository,
    workspace_id: str,
    skill_id: str,
    revision_id: str,
) -> None:
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        _ = connection.execute(
            """
            UPDATE skills SET display_revision_id=?
            WHERE workspace_id=? AND skill_id=?
            AND EXISTS (
                SELECT 1 FROM skill_heads
                WHERE workspace_id=? AND skill_id=? AND revision_id=?
            )
            """,
            (revision_id, workspace_id, skill_id, workspace_id, skill_id, revision_id),
        )


__all__ = [
    "assert_skill_head",
    "insert_skill_revision",
    "mark_skill_display_current",
    "prepare_skill_write",
    "read_skill",
    "skill_ids",
]

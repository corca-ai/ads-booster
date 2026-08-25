from __future__ import annotations

import time
from typing import Final
from uuid import uuid4

from trace_capture.workspace.database import SqliteCursor, WorkspaceRepositoryBase
from trace_capture.workspace.errors import (
    RevisionConflictError,
    ScopedRecordNotFoundError,
    WorkspaceStoreCorruptionError,
)
from trace_capture.workspace.models import (
    ContextCreate,
    ContextId,
    ContextKind,
    ContextRecord,
    WorkspaceId,
)

type ContextRow = tuple[str, str, str, str, str, int, float, float]
_CONTEXT: Final = "context"


class ContextStore(WorkspaceRepositoryBase):
    def create_context(self, workspace_id: WorkspaceId, value: ContextCreate) -> ContextRecord:
        context_id = ContextId(uuid4().hex)
        now = time.time()
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                "INSERT INTO contexts VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (workspace_id, context_id, value.kind, value.title, value.body, now, now),
            )
        return ContextRecord(
            workspace_id=workspace_id,
            context_id=context_id,
            kind=value.kind,
            title=value.title,
            body=value.body,
            revision=1,
            created_at=now,
            updated_at=now,
        )

    def get_context(self, workspace_id: WorkspaceId, context_id: ContextId) -> ContextRecord:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                """
                SELECT workspace_id, context_id, kind, title, body, revision, created_at, updated_at
                FROM contexts WHERE workspace_id = ? AND context_id = ?
                """,
                (workspace_id, context_id),
            )
            row = _fetch_context(cursor)
        if row is None:
            raise ScopedRecordNotFoundError(record_type=_CONTEXT, record_id=context_id)
        return _context_from_row(row)

    def list_contexts(self, workspace_id: WorkspaceId) -> tuple[ContextRecord, ...]:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                """
                SELECT workspace_id, context_id, kind, title, body, revision, created_at, updated_at
                FROM contexts WHERE workspace_id = ? ORDER BY created_at, context_id
                """,
                (workspace_id,),
            )
            rows: list[ContextRecord] = []
            while (row := _fetch_context(cursor)) is not None:
                rows.append(_context_from_row(row))
        return tuple(rows)

    def update_context(
        self,
        workspace_id: WorkspaceId,
        context_id: ContextId,
        value: ContextCreate,
        *,
        expected_revision: int,
    ) -> ContextRecord:
        now = time.time()
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE contexts
                SET kind = ?, title = ?, body = ?, revision = revision + 1, updated_at = ?
                WHERE workspace_id = ? AND context_id = ? AND revision = ?
                """,
                (
                    value.kind,
                    value.title,
                    value.body,
                    now,
                    workspace_id,
                    context_id,
                    expected_revision,
                ),
            )
            if result.rowcount != 1:
                raise RevisionConflictError(
                    record_type=_CONTEXT,
                    record_id=context_id,
                    expected_revision=expected_revision,
                )
        return self.get_context(workspace_id, context_id)

    def delete_context(
        self,
        workspace_id: WorkspaceId,
        context_id: ContextId,
        *,
        expected_revision: int,
    ) -> None:
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                """
                DELETE FROM contexts
                WHERE workspace_id = ? AND context_id = ? AND revision = ?
                """,
                (workspace_id, context_id, expected_revision),
            )
            if result.rowcount == 1:
                return
            cursor: SqliteCursor = connection.execute(
                "SELECT 1 FROM contexts WHERE workspace_id = ? AND context_id = ?",
                (workspace_id, context_id),
            )
            if cursor.fetchone() is None:
                raise ScopedRecordNotFoundError(record_type=_CONTEXT, record_id=context_id)
            raise RevisionConflictError(
                record_type=_CONTEXT,
                record_id=context_id,
                expected_revision=expected_revision,
            )


def _context_from_row(row: ContextRow) -> ContextRecord:
    return ContextRecord(
        workspace_id=WorkspaceId(row[0]),
        context_id=ContextId(row[1]),
        kind=ContextKind(row[2]),
        title=row[3],
        body=row[4],
        revision=row[5],
        created_at=row[6],
        updated_at=row[7],
    )


def _fetch_context(cursor: SqliteCursor) -> ContextRow | None:
    match cursor.fetchone():
        case None:
            return None
        case (
            str() as workspace_id,
            str() as context_id,
            str() as kind,
            str() as title,
            str() as body,
            int() as revision,
            float() as created_at,
            float() as updated_at,
        ):
            return workspace_id, context_id, kind, title, body, revision, created_at, updated_at
        case _:
            raise WorkspaceStoreCorruptionError(record_type=_CONTEXT)

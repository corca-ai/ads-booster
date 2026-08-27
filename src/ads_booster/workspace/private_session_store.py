from __future__ import annotations

import time
from typing import Final
from uuid import uuid4

from pydantic import TypeAdapter

from ads_booster.transport.json_types import JsonObject
from ads_booster.workspace.database import SqliteCursor, WorkspaceRepositoryBase
from ads_booster.workspace.errors import (
    RevisionConflictError,
    ScopedRecordNotFoundError,
    WorkspaceStoreCorruptionError,
)
from ads_booster.workspace.models import (
    MemberId,
    PrivateSessionCreate,
    PrivateSessionId,
    PrivateSessionRecord,
    WorkspaceId,
)

type SessionRow = tuple[str, str, str, str, str, int, float, float]

_HISTORY_ADAPTER = TypeAdapter(tuple[JsonObject, ...])
_PRIVATE_SESSION: Final = "private session"


class PrivateSessionStore(WorkspaceRepositoryBase):
    def create_private_session(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        value: PrivateSessionCreate,
    ) -> PrivateSessionRecord:
        session_id = PrivateSessionId(uuid4().hex)
        now = time.time()
        history_json = _HISTORY_ADAPTER.dump_json(value.history).decode()
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                "INSERT INTO private_sessions VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (workspace_id, member_id, session_id, value.title, history_json, now, now),
            )
        return PrivateSessionRecord(
            workspace_id=workspace_id,
            member_id=member_id,
            session_id=session_id,
            title=value.title,
            history=value.history,
            revision=1,
            created_at=now,
            updated_at=now,
        )

    def get_private_session(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        session_id: PrivateSessionId,
    ) -> PrivateSessionRecord:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                """
                SELECT workspace_id, member_id, session_id, title, history_json, revision,
                       created_at, updated_at
                FROM private_sessions
                WHERE workspace_id = ? AND member_id = ? AND session_id = ?
                """,
                (workspace_id, member_id, session_id),
            )
            row = _fetch_session(cursor)
        if row is None:
            raise ScopedRecordNotFoundError(record_type=_PRIVATE_SESSION, record_id=session_id)
        return _session_from_row(row)

    def list_private_sessions(
        self, workspace_id: WorkspaceId, member_id: MemberId
    ) -> tuple[PrivateSessionRecord, ...]:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                """
                SELECT workspace_id, member_id, session_id, title, history_json, revision,
                       created_at, updated_at
                FROM private_sessions
                WHERE workspace_id = ? AND member_id = ?
                ORDER BY updated_at DESC, session_id
                """,
                (workspace_id, member_id),
            )
            rows: list[PrivateSessionRecord] = []
            while (row := _fetch_session(cursor)) is not None:
                rows.append(_session_from_row(row))
        return tuple(rows)

    def update_private_session(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        session_id: PrivateSessionId,
        *,
        expected_revision: int,
        history: tuple[JsonObject, ...],
    ) -> PrivateSessionRecord:
        now = time.time()
        history_json = _HISTORY_ADAPTER.dump_json(history).decode()
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE private_sessions
                SET history_json = ?, revision = revision + 1, updated_at = ?
                WHERE workspace_id = ? AND member_id = ? AND session_id = ? AND revision = ?
                """,
                (history_json, now, workspace_id, member_id, session_id, expected_revision),
            )
            if result.rowcount != 1:
                raise RevisionConflictError(
                    record_type=_PRIVATE_SESSION,
                    record_id=session_id,
                    expected_revision=expected_revision,
                )
        return self.get_private_session(workspace_id, member_id, session_id)

    def delete_private_session(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        session_id: PrivateSessionId,
    ) -> None:
        with self._database.connect(write=True) as connection:
            query = "DELETE FROM private_sessions WHERE workspace_id = ? AND member_id = ? AND session_id = ?"  # noqa: E501
            _ = connection.execute(
                query,
                (workspace_id, member_id, session_id),
            )


def _session_from_row(row: SessionRow) -> PrivateSessionRecord:
    return PrivateSessionRecord(
        workspace_id=WorkspaceId(row[0]),
        member_id=MemberId(row[1]),
        session_id=PrivateSessionId(row[2]),
        title=row[3],
        history=_HISTORY_ADAPTER.validate_json(row[4]),
        revision=row[5],
        created_at=row[6],
        updated_at=row[7],
    )


def _fetch_session(cursor: SqliteCursor) -> SessionRow | None:
    match cursor.fetchone():
        case None:
            return None
        case (
            str() as workspace_id,
            str() as member_id,
            str() as session_id,
            str() as title,
            str() as history_json,
            int() as revision,
            float() as created_at,
            float() as updated_at,
        ):
            return (
                workspace_id,
                member_id,
                session_id,
                title,
                history_json,
                revision,
                created_at,
                updated_at,
            )
        case _:
            raise WorkspaceStoreCorruptionError(record_type=_PRIVATE_SESSION)

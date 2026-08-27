from __future__ import annotations

import time
from typing import Final
from uuid import uuid4

from ads_booster.workspace.database import SqliteCursor, WorkspaceRepositoryBase
from ads_booster.workspace.errors import (
    RevisionConflictError,
    ScopedRecordNotFoundError,
    WorkspaceStoreCorruptionError,
)
from ads_booster.workspace.models import (
    MemberId,
    MemberRecord,
    ProvisionedMember,
    ProvisionedWorkspace,
    WorkspaceId,
    WorkspaceRecord,
)
from ads_booster.workspace.secrets import issue_code, verify_code

type WorkspaceRow = tuple[str, str, str, int, float, float]
type MemberRow = tuple[str, str, str, str, int, float, float]
_WORKSPACE: Final = "workspace"
_WORKSPACE_CODE: Final = "workspace code"
_MEMBER: Final = "member"
_MEMBER_CODE: Final = "member code"


class IdentityStore(WorkspaceRepositoryBase):
    def create_workspace(self, name: str) -> ProvisionedWorkspace:
        workspace_id = WorkspaceId(uuid4().hex)
        code, code_hash = issue_code()
        now = time.time()
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                "INSERT INTO workspaces VALUES (?, ?, ?, 1, ?, ?)",
                (workspace_id, name, code_hash, now, now),
            )
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            name=name,
            code_version=1,
            created_at=now,
            updated_at=now,
        )
        return ProvisionedWorkspace(workspace=record, access_code=code)

    def rotate_workspace_code(
        self, workspace_id: WorkspaceId, *, expected_version: int
    ) -> ProvisionedWorkspace:
        code, code_hash = issue_code()
        now = time.time()
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE workspaces SET code_hash = ?, code_version = code_version + 1, updated_at = ?
                WHERE workspace_id = ? AND code_version = ?
                """,
                (code_hash, now, workspace_id, expected_version),
            )
            if result.rowcount != 1:
                raise RevisionConflictError(
                    record_type=_WORKSPACE_CODE,
                    record_id=workspace_id,
                    expected_revision=expected_version,
                )
        return ProvisionedWorkspace(
            workspace=self.get_workspace(workspace_id),
            access_code=code,
        )

    def verify_workspace_code(self, workspace_id: WorkspaceId, code: str) -> bool:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                "SELECT code_hash FROM workspaces WHERE workspace_id = ?", (workspace_id,)
            )
            row = _fetch_code_hash(cursor)
        return False if row is None else verify_code(code, row[0])

    def get_workspace(self, workspace_id: WorkspaceId) -> WorkspaceRecord:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                """
                SELECT workspace_id, name, code_hash, code_version, created_at, updated_at
                FROM workspaces WHERE workspace_id = ?
                """,
                (workspace_id,),
            )
            row = _fetch_workspace(cursor)
        if row is None:
            raise ScopedRecordNotFoundError(record_type=_WORKSPACE, record_id=workspace_id)
        return _workspace_from_row(row)

    def rename_workspace(self, workspace_id: WorkspaceId, name: str) -> WorkspaceRecord:
        now = time.time()
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                "UPDATE workspaces SET name = ?, updated_at = ? WHERE workspace_id = ?",
                (name, now, workspace_id),
            )
            if result.rowcount != 1:
                raise ScopedRecordNotFoundError(record_type=_WORKSPACE, record_id=workspace_id)
        return self.get_workspace(workspace_id)

    def create_member(self, workspace_id: WorkspaceId, display_name: str) -> ProvisionedMember:
        member_id = MemberId(uuid4().hex)
        code, code_hash = issue_code()
        now = time.time()
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                "INSERT INTO members VALUES (?, ?, ?, ?, 1, ?, ?)",
                (workspace_id, member_id, display_name, code_hash, now, now),
            )
        member = MemberRecord(
            workspace_id=workspace_id,
            member_id=member_id,
            display_name=display_name,
            code_version=1,
            created_at=now,
            updated_at=now,
        )
        return ProvisionedMember(member=member, invite_code=code)

    def rotate_member_code(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        *,
        expected_version: int,
    ) -> ProvisionedMember:
        code, code_hash = issue_code()
        now = time.time()
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE members SET code_hash = ?, code_version = code_version + 1, updated_at = ?
                WHERE workspace_id = ? AND member_id = ? AND code_version = ?
                """,
                (code_hash, now, workspace_id, member_id, expected_version),
            )
            if result.rowcount != 1:
                raise RevisionConflictError(
                    record_type=_MEMBER_CODE,
                    record_id=member_id,
                    expected_revision=expected_version,
                )
        return ProvisionedMember(
            member=self.get_member(workspace_id, member_id),
            invite_code=code,
        )

    def verify_member_code(self, workspace_id: WorkspaceId, member_id: MemberId, code: str) -> bool:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                "SELECT code_hash FROM members WHERE workspace_id = ? AND member_id = ?",
                (workspace_id, member_id),
            )
            row = _fetch_code_hash(cursor)
        return False if row is None else verify_code(code, row[0])

    def get_member(self, workspace_id: WorkspaceId, member_id: MemberId) -> MemberRecord:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                """
                SELECT workspace_id, member_id, display_name, code_hash, code_version,
                       created_at, updated_at
                FROM members WHERE workspace_id = ? AND member_id = ?
                """,
                (workspace_id, member_id),
            )
            row = _fetch_member(cursor)
        if row is None:
            raise ScopedRecordNotFoundError(record_type=_MEMBER, record_id=member_id)
        return _member_from_row(row)


def _workspace_from_row(row: WorkspaceRow) -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_id=WorkspaceId(row[0]),
        name=row[1],
        code_version=row[3],
        created_at=row[4],
        updated_at=row[5],
    )


def _member_from_row(row: MemberRow) -> MemberRecord:
    return MemberRecord(
        workspace_id=WorkspaceId(row[0]),
        member_id=MemberId(row[1]),
        display_name=row[2],
        code_version=row[4],
        created_at=row[5],
        updated_at=row[6],
    )


def _fetch_code_hash(cursor: SqliteCursor) -> tuple[str] | None:
    match cursor.fetchone():
        case None:
            return None
        case (str() as code_hash,):
            return (code_hash,)
        case _:
            raise WorkspaceStoreCorruptionError(record_type="code hash")


def _fetch_workspace(cursor: SqliteCursor) -> WorkspaceRow | None:
    match cursor.fetchone():
        case None:
            return None
        case (
            str() as workspace_id,
            str() as name,
            str() as code_hash,
            int() as code_version,
            float() as created_at,
            float() as updated_at,
        ):
            return workspace_id, name, code_hash, code_version, created_at, updated_at
        case _:
            raise WorkspaceStoreCorruptionError(record_type=_WORKSPACE)


def _fetch_member(cursor: SqliteCursor) -> MemberRow | None:
    match cursor.fetchone():
        case None:
            return None
        case (
            str() as workspace_id,
            str() as member_id,
            str() as display_name,
            str() as code_hash,
            int() as code_version,
            float() as created_at,
            float() as updated_at,
        ):
            return (
                workspace_id,
                member_id,
                display_name,
                code_hash,
                code_version,
                created_at,
                updated_at,
            )
        case _:
            raise WorkspaceStoreCorruptionError(record_type=_MEMBER)

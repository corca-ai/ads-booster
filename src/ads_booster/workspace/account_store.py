"""Marketing accounts: the concept a country's posts are written from.

An account is the unit the team looks at, so where the rows live is a deployment question
rather than a product one — the local workspace keeps them in the same SQLite file as its
candidates, and the hosted control plane will want them in D1. `MarketingAccountReader` and
`MarketingAccountWriter` are the shape everything above this module is written against, so
the second implementation is an addition rather than a rewrite of the callers.
"""

from __future__ import annotations

import json
import time
from typing import Final, Protocol
from uuid import uuid4

from pydantic import ValidationError

from ads_booster.workspace.database import SqliteCursor, WorkspaceRepositoryBase
from ads_booster.workspace.errors import (
    RevisionConflictError,
    ScopedRecordNotFoundError,
    WorkspaceStoreCorruptionError,
)
from ads_booster.workspace.models import (
    MarketingAccountCreate,
    MarketingAccountId,
    MarketingAccountIdentity,
    MarketingAccountRecord,
    MarketingAccountStatus,
    WorkspaceId,
)

type AccountRow = tuple[str, str, str, str, str, str, int, float, float]
_ACCOUNT: Final = "marketing_account"
_COLUMNS: Final[str] = (
    "workspace_id, account_id, country, identity_json, status, note, revision, created_at, updated_at"  # noqa: E501
)
_TABLE: Final[str] = "marketing_accounts"
_SELECT_ALL: Final[str] = (
    f"SELECT {_COLUMNS} FROM {_TABLE} WHERE workspace_id = ? ORDER BY created_at DESC, account_id"  # noqa: S608
)
_SELECT_ONE: Final[str] = (
    f"SELECT {_COLUMNS} FROM {_TABLE} WHERE workspace_id = ? AND account_id = ?"  # noqa: S608
)


class MarketingAccountReader(Protocol):
    """Read access to the accounts of one workspace."""

    def list_accounts(self, workspace_id: WorkspaceId) -> tuple[MarketingAccountRecord, ...]: ...

    def get_account(
        self,
        workspace_id: WorkspaceId,
        account_id: MarketingAccountId,
    ) -> MarketingAccountRecord: ...


class MarketingAccountWriter(MarketingAccountReader, Protocol):
    """Write access, including the human verdicts that move an account's status."""

    def create_account(
        self,
        workspace_id: WorkspaceId,
        value: MarketingAccountCreate,
    ) -> MarketingAccountRecord: ...

    def update_account(
        self,
        workspace_id: WorkspaceId,
        account_id: MarketingAccountId,
        *,
        identity: MarketingAccountIdentity,
        note: str,
        expected_revision: int,
    ) -> MarketingAccountRecord: ...

    def set_account_status(
        self,
        workspace_id: WorkspaceId,
        account_id: MarketingAccountId,
        *,
        status: MarketingAccountStatus,
        expected_revision: int,
    ) -> MarketingAccountRecord: ...


class SqliteMarketingAccountStore(WorkspaceRepositoryBase):
    """The local implementation of the account protocols."""

    def create_account(
        self,
        workspace_id: WorkspaceId,
        value: MarketingAccountCreate,
    ) -> MarketingAccountRecord:
        account_id = MarketingAccountId(uuid4().hex)
        now = time.time()
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                "INSERT INTO marketing_accounts VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    workspace_id,
                    account_id,
                    value.country,
                    value.identity.model_dump_json(),
                    value.status,
                    value.note,
                    now,
                    now,
                ),
            )
        return MarketingAccountRecord(
            workspace_id=workspace_id,
            account_id=account_id,
            country=value.country,
            identity=value.identity,
            status=value.status,
            note=value.note,
            revision=1,
            created_at=now,
            updated_at=now,
        )

    def list_accounts(self, workspace_id: WorkspaceId) -> tuple[MarketingAccountRecord, ...]:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                _SELECT_ALL,
                (workspace_id,),
            )
            records: list[MarketingAccountRecord] = []
            while (row := _fetch_account(cursor)) is not None:
                records.append(_account_from_row(row))
        return tuple(records)

    def get_account(
        self,
        workspace_id: WorkspaceId,
        account_id: MarketingAccountId,
    ) -> MarketingAccountRecord:
        row = self._row(workspace_id, account_id)
        return _account_from_row(row)

    def update_account(
        self,
        workspace_id: WorkspaceId,
        account_id: MarketingAccountId,
        *,
        identity: MarketingAccountIdentity,
        note: str,
        expected_revision: int,
    ) -> MarketingAccountRecord:
        current = _account_from_row(self._row(workspace_id, account_id))
        _require_revision(current, expected_revision)
        now = time.time()
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                """
                UPDATE marketing_accounts
                SET identity_json = ?, note = ?, revision = ?, updated_at = ?
                WHERE workspace_id = ? AND account_id = ? AND revision = ?
                """,
                (
                    identity.model_dump_json(),
                    note,
                    current.revision + 1,
                    now,
                    workspace_id,
                    account_id,
                    expected_revision,
                ),
            )
        return current.model_copy(
            update={
                "identity": identity,
                "note": note,
                "revision": current.revision + 1,
                "updated_at": now,
            }
        )

    def set_account_status(
        self,
        workspace_id: WorkspaceId,
        account_id: MarketingAccountId,
        *,
        status: MarketingAccountStatus,
        expected_revision: int,
    ) -> MarketingAccountRecord:
        current = _account_from_row(self._row(workspace_id, account_id))
        _require_revision(current, expected_revision)
        now = time.time()
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                """
                UPDATE marketing_accounts
                SET status = ?, revision = ?, updated_at = ?
                WHERE workspace_id = ? AND account_id = ? AND revision = ?
                """,
                (status, current.revision + 1, now, workspace_id, account_id, expected_revision),
            )
        return current.model_copy(
            update={"status": status, "revision": current.revision + 1, "updated_at": now}
        )

    def _row(self, workspace_id: WorkspaceId, account_id: MarketingAccountId) -> AccountRow:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                _SELECT_ONE,
                (workspace_id, account_id),
            )
            row = _fetch_account(cursor)
        if row is None:
            raise ScopedRecordNotFoundError(record_type=_ACCOUNT, record_id=account_id)
        return row


def _require_revision(current: MarketingAccountRecord, expected: int) -> None:
    if current.revision != expected:
        raise RevisionConflictError(
            record_type=_ACCOUNT,
            record_id=current.account_id,
            expected_revision=expected,
        )


def _fetch_account(cursor: SqliteCursor) -> AccountRow | None:
    match cursor.fetchone():
        case None:
            return None
        case (
            str() as workspace_id,
            str() as account_id,
            str() as country,
            str() as identity_json,
            str() as status,
            str() as note,
            int() as revision,
            float() as created_at,
            float() as updated_at,
        ):
            return (
                workspace_id,
                account_id,
                country,
                identity_json,
                status,
                note,
                revision,
                created_at,
                updated_at,
            )
        case _:
            raise WorkspaceStoreCorruptionError(record_type=_ACCOUNT)


def _account_from_row(row: AccountRow) -> MarketingAccountRecord:
    try:
        identity = MarketingAccountIdentity.model_validate(json.loads(row[3]))
        status = MarketingAccountStatus(row[4])
    except (ValidationError, ValueError, TypeError) as error:
        raise WorkspaceStoreCorruptionError(record_type=_ACCOUNT) from error
    return MarketingAccountRecord(
        workspace_id=WorkspaceId(row[0]),
        account_id=MarketingAccountId(row[1]),
        country=row[2],
        identity=identity,
        status=status,
        note=row[5],
        revision=row[6],
        created_at=row[7],
        updated_at=row[8],
    )

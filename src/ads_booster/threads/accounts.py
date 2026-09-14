from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_urlsafe

from pydantic import TypeAdapter

from ..contracts.threads import ThreadsAccount, ThreadsAccountStatus


class ThreadsAccountConflictError(ValueError):
    pass


_OPTIONAL_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])


@dataclass(frozen=True, slots=True)
class ThreadsTokenVault:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def put(self, token: str, *, token_ref: str | None = None) -> str:
        reference = token_urlsafe(24) if token_ref is None else token_ref
        self._require_token_value(token, reference)
        path = self.root / reference
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(path, flags, 0o600)
        try:
            _ = os.write(descriptor, token.encode())
            os.fsync(descriptor)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            os.close(descriptor)
        return reference

    def replace(self, token_ref: str, token: str) -> None:
        self._require_token_value(token, token_ref)
        path = self.root / token_ref
        temporary = self.root / f".{token_ref}.{token_urlsafe(8)}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            try:
                _ = os.write(descriptor, token.encode())
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def get(self, token_ref: str) -> str:
        if "/" in token_ref or token_ref in {".", ".."}:
            raise ThreadsAccountConflictError("threads_token_ref_invalid")
        path = self.root / token_ref
        if path.stat().st_mode & 0o077:
            raise ThreadsAccountConflictError("threads_token_permissions_invalid")
        return path.read_text(encoding="utf-8")

    def delete(self, token_ref: str) -> None:
        if "/" in token_ref or token_ref in {".", ".."}:
            raise ThreadsAccountConflictError("threads_token_ref_invalid")
        (self.root / token_ref).unlink(missing_ok=True)

    @staticmethod
    def _require_token_value(token: str, token_ref: str) -> None:
        if not token or "/" in token_ref or token_ref in {".", ".."}:
            raise ThreadsAccountConflictError("threads_token_invalid")


@dataclass(frozen=True, slots=True)
class ThreadsAccountRepository:
    database_path: Path

    def __post_init__(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.executescript(
                """
                CREATE TABLE IF NOT EXISTS threads_accounts (
                    connection_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    owner_member_id TEXT NOT NULL,
                    provider_account_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    status TEXT NOT NULL,
                    account_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, provider_account_id)
                );
                CREATE INDEX IF NOT EXISTS threads_accounts_owner
                    ON threads_accounts(workspace_id, owner_member_id, status);
                """
            )

    def put(self, account: ThreadsAccount) -> ThreadsAccount:
        current = self.get(account.workspace_id, account.connection_id)
        if current is not None and current.owner_member_id != account.owner_member_id:
            raise ThreadsAccountConflictError("threads_account_owner_conflict")
        try:
            with closing(sqlite3.connect(self.database_path)) as database, database:
                cursor = database.execute(
                    """INSERT INTO threads_accounts(
                    connection_id,workspace_id,owner_member_id,provider_account_id,
                    username,status,account_json,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(connection_id) DO UPDATE SET
                    username=excluded.username,status=excluded.status,
                    account_json=excluded.account_json,updated_at=excluded.updated_at
                    WHERE threads_accounts.workspace_id=excluded.workspace_id
                    AND threads_accounts.owner_member_id=excluded.owner_member_id
                    AND threads_accounts.provider_account_id=excluded.provider_account_id""",
                    (
                        account.connection_id,
                        account.workspace_id,
                        account.owner_member_id,
                        account.provider_account_id,
                        account.username,
                        account.status,
                        account.model_dump_json(),
                        account.updated_at.isoformat(),
                    ),
                )
                if cursor.rowcount != 1:
                    raise ThreadsAccountConflictError("threads_account_identity_conflict")
        except sqlite3.IntegrityError as error:
            raise ThreadsAccountConflictError(
                "threads_provider_account_already_connected"
            ) from error
        return account

    def get(self, workspace_id: str, connection_id: str) -> ThreadsAccount | None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    """SELECT account_json FROM threads_accounts
                    WHERE workspace_id=? AND connection_id=?""",
                    (workspace_id, connection_id),
                ).fetchone()
            )
        if row is None:
            return None
        return ThreadsAccount.model_validate_json(row[0])

    def list_for_workspace(
        self, workspace_id: str, *, owner_member_id: str | None = None
    ) -> tuple[ThreadsAccount, ...]:
        query = "SELECT account_json FROM threads_accounts WHERE workspace_id=?"
        values: tuple[str, ...] = (workspace_id,)
        if owner_member_id is not None:
            query += " AND owner_member_id=?"
            values = (workspace_id, owner_member_id)
        query += " ORDER BY username, connection_id"
        with closing(sqlite3.connect(self.database_path)) as database, database:
            rows = _ROWS.validate_python(database.execute(query, values).fetchall())
        return tuple(ThreadsAccount.model_validate_json(row[0]) for row in rows)

    def require_owner(
        self, workspace_id: str, connection_id: str, member_id: str
    ) -> ThreadsAccount:
        account = self.require_owned(workspace_id, connection_id, member_id)
        if account.status is not ThreadsAccountStatus.ACTIVE:
            raise ThreadsAccountConflictError("threads_account_inactive")
        return account

    def require_owned(
        self, workspace_id: str, connection_id: str, member_id: str
    ) -> ThreadsAccount:
        account = self.get(workspace_id, connection_id)
        if account is None:
            raise ThreadsAccountConflictError("threads_account_not_found")
        if account.owner_member_id != member_id:
            raise ThreadsAccountConflictError("threads_account_owner_required")
        return account

    def require_readable(
        self, workspace_id: str, connection_id: str, *, now: datetime | None = None
    ) -> ThreadsAccount:
        account = self.get(workspace_id, connection_id)
        if account is None:
            raise ThreadsAccountConflictError("threads_account_not_found")
        current = datetime.now(UTC) if now is None else now
        if account.status is not ThreadsAccountStatus.ACTIVE:
            raise ThreadsAccountConflictError("threads_account_inactive")
        if account.expires_at <= current:
            raise ThreadsAccountConflictError("threads_account_reauth_required")
        return account

    def mark_reauth(self, account: ThreadsAccount, *, now: datetime) -> ThreadsAccount:
        return self.put(
            account.model_copy(
                update={"status": ThreadsAccountStatus.REAUTH_REQUIRED, "updated_at": now}
            )
        )


__all__ = [
    "ThreadsAccountConflictError",
    "ThreadsAccountRepository",
    "ThreadsTokenVault",
]

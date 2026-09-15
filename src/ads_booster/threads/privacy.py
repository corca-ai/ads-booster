from __future__ import annotations

import hmac
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic field.
from hashlib import sha256
from secrets import token_hex
from typing import TYPE_CHECKING, Annotated, Final, Literal

from pydantic import Field, TypeAdapter

from ads_booster.contracts.models import ContractModel, Identifier
from ads_booster.contracts.threads import ThreadsAccount, ThreadsPublicationReceipt
from ads_booster.threads.callback_urls import DATA_DELETION_STATUS_PREFIX
from ads_booster.threads.drafts import ThreadsDraftBatch
from ads_booster.threads.provider_callbacks import verify_meta_signed_request

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault


class ThreadsDeletionReceipt(ContractModel):
    confirmation_code: Annotated[Identifier, Field(pattern=r"^[0-9a-f]{32}$")]
    status: Literal["pending", "completed"]
    requested_at: datetime
    completed_at: datetime | None = None


_OPTIONAL_RECEIPT_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_ACCOUNT_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_JSON_ROWS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])
_DELETE_TRIGGER_NAMES: Final = (
    "provider_metric_snapshots_append_only",
    "threads_publication_events_append_only",
    "threads_draft_revisions_append_only",
)
_RESTORE_DELETE_TRIGGERS: Final = (
    """CREATE TRIGGER provider_metric_snapshots_append_only
    BEFORE DELETE ON provider_metric_snapshots BEGIN
        SELECT RAISE(ABORT, 'provider metric snapshots are append-only');
    END""",
    """CREATE TRIGGER threads_publication_events_append_only
    BEFORE DELETE ON threads_publication_events BEGIN
        SELECT RAISE(ABORT, 'threads publication events are append-only');
    END""",
    """CREATE TRIGGER threads_draft_revisions_append_only
    BEFORE DELETE ON threads_draft_revisions BEGIN
        SELECT RAISE(ABORT, 'threads draft revisions are append-only');
    END""",
)


@dataclass(frozen=True, slots=True)
class ThreadsPrivacyCallbacks:
    database_path: Path
    public_origin: str
    app_secret: str
    accounts: ThreadsAccountRepository
    tokens: ThreadsTokenVault

    def __post_init__(self) -> None:
        """Create the durable provider deletion receipt store."""
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.execute(
                """CREATE TABLE IF NOT EXISTS threads_data_deletions (
                confirmation_code TEXT PRIMARY KEY,
                subject_hmac TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                receipt_json TEXT NOT NULL
                )"""
            )

    def deauthorize(self, signed_value: str, *, now: datetime) -> int:
        provider = verify_meta_signed_request(signed_value, self.app_secret)
        accounts = self.accounts.revoke_provider(provider.user_id, now=now)
        for account in accounts:
            self.tokens.delete(account.token_ref)
        return len(accounts)

    def delete(self, signed_value: str, *, now: datetime) -> ThreadsDeletionReceipt:
        provider = verify_meta_signed_request(signed_value, self.app_secret)
        subject_hmac = hmac.new(
            self.app_secret.encode(), f"delete-request\n{signed_value}".encode(), sha256
        ).hexdigest()
        receipt = self._claim(subject_hmac, now=now)
        if receipt.status == "completed":
            return receipt
        self._delete_provider_data(provider.user_id)
        completed = receipt.model_copy(update={"status": "completed", "completed_at": now})
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.execute(
                """UPDATE threads_data_deletions SET status='completed',receipt_json=?
                WHERE confirmation_code=? AND status='pending'""",
                (completed.model_dump_json(), completed.confirmation_code),
            )
        return completed

    def status(self, confirmation_code: str) -> ThreadsDeletionReceipt | None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            row = _OPTIONAL_RECEIPT_ROW.validate_python(
                database.execute(
                    "SELECT receipt_json FROM threads_data_deletions WHERE confirmation_code=?",
                    (confirmation_code,),
                ).fetchone()
            )
        return None if row is None else ThreadsDeletionReceipt.model_validate_json(row[0])

    def status_url(self, confirmation_code: str) -> str:
        return self.public_origin.rstrip("/") + DATA_DELETION_STATUS_PREFIX + confirmation_code

    def _claim(self, subject_hmac: str, *, now: datetime) -> ThreadsDeletionReceipt:
        candidate = ThreadsDeletionReceipt(
            confirmation_code=token_hex(16), status="pending", requested_at=now
        )
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.execute("BEGIN IMMEDIATE")
            _ = database.execute(
                """INSERT OR IGNORE INTO threads_data_deletions(
                confirmation_code,subject_hmac,status,receipt_json) VALUES(?,?,?,?)""",
                (
                    candidate.confirmation_code,
                    subject_hmac,
                    candidate.status,
                    candidate.model_dump_json(),
                ),
            )
            row = _OPTIONAL_RECEIPT_ROW.validate_python(
                database.execute(
                    "SELECT receipt_json FROM threads_data_deletions WHERE subject_hmac=?",
                    (subject_hmac,),
                ).fetchone()
            )
        if row is None:
            message = "threads_deletion_claim_missing"
            raise RuntimeError(message)
        return ThreadsDeletionReceipt.model_validate_json(row[0])

    def _delete_provider_data(self, provider_user_id: str) -> None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.execute("BEGIN EXCLUSIVE")
            rows = _ACCOUNT_ROWS.validate_python(
                database.execute(
                    "SELECT account_json FROM threads_accounts WHERE provider_account_id=?",
                    (provider_user_id,),
                ).fetchall()
            )
            accounts = tuple(ThreadsAccount.model_validate_json(row[0]) for row in rows)
            connection_ids = frozenset(account.connection_id for account in accounts)
            for account in accounts:
                self.tokens.delete(account.token_ref)
            if not connection_ids:
                return
            batch_ids = self._batch_ids(database, connection_ids)
            operation_ids = self._operation_ids(database, connection_ids)
            for trigger_name in _DELETE_TRIGGER_NAMES:
                _ = database.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
            _delete_values(database, "threads_publication_events", "operation_id", operation_ids)
            _delete_values(database, "threads_publications", "operation_id", operation_ids)
            _delete_values(
                database, "provider_metric_snapshots", "connection_id", connection_ids
            )
            _delete_values(database, "threads_media_grants", "batch_id", batch_ids)
            _delete_values(database, "threads_draft_revisions", "batch_id", batch_ids)
            _delete_values(database, "threads_draft_batches", "batch_id", batch_ids)
            _ = database.execute(
                "DELETE FROM threads_accounts WHERE provider_account_id=?",
                (provider_user_id,),
            )
            for statement in _RESTORE_DELETE_TRIGGERS:
                _ = database.execute(statement)

    @staticmethod
    def _batch_ids(
        database: sqlite3.Connection, connection_ids: frozenset[str]
    ) -> frozenset[str]:
        rows = _JSON_ROWS.validate_python(
            database.execute(
                """SELECT batch_id,batch_json FROM threads_draft_revisions
                UNION SELECT batch_id,batch_json FROM threads_draft_batches"""
            ).fetchall()
        )
        return frozenset(
            batch_id
            for batch_id, batch_json in rows
            if any(
                item.connection_id in connection_ids
                for item in ThreadsDraftBatch.model_validate_json(batch_json).items
            )
        )

    @staticmethod
    def _operation_ids(
        database: sqlite3.Connection, connection_ids: frozenset[str]
    ) -> frozenset[str]:
        rows = _JSON_ROWS.validate_python(
            database.execute(
                "SELECT operation_id,receipt_json FROM threads_publications"
            ).fetchall()
        )
        return frozenset(
            operation_id
            for operation_id, receipt_json in rows
            if ThreadsPublicationReceipt.model_validate_json(receipt_json).connection_id
            in connection_ids
        )


def _delete_values(
    database: sqlite3.Connection,
    table: Literal[
        "provider_metric_snapshots",
        "threads_draft_batches",
        "threads_draft_revisions",
        "threads_media_grants",
        "threads_publication_events",
        "threads_publications",
    ],
    column: Literal["batch_id", "connection_id", "operation_id"],
    values: frozenset[str],
) -> None:
    if not values:
        return
    queries = {
        ("threads_publication_events", "operation_id"): (
            "DELETE FROM threads_publication_events WHERE operation_id=?"
        ),
        ("threads_publications", "operation_id"): (
            "DELETE FROM threads_publications WHERE operation_id=?"
        ),
        ("provider_metric_snapshots", "connection_id"): (
            "DELETE FROM provider_metric_snapshots WHERE connection_id=?"
        ),
        ("threads_media_grants", "batch_id"): (
            "DELETE FROM threads_media_grants WHERE batch_id=?"
        ),
        ("threads_draft_revisions", "batch_id"): (
            "DELETE FROM threads_draft_revisions WHERE batch_id=?"
        ),
        ("threads_draft_batches", "batch_id"): (
            "DELETE FROM threads_draft_batches WHERE batch_id=?"
        ),
    }
    _ = database.executemany(queries[(table, column)], ((value,) for value in sorted(values)))


__all__ = ["ThreadsDeletionReceipt", "ThreadsPrivacyCallbacks"]

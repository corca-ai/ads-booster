from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

from pydantic import TypeAdapter

from ads_booster.contracts.threads import ThreadsPublicationReceipt
from ads_booster.providers.threads_api import ThreadsApiClient, ThreadsApiError
from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault
from ads_booster.threads.drafts import (
    ThreadsDraftAction,
    ThreadsDraftBatch,
    ThreadsDraftRepository,
    ThreadsDraftState,
)
from ads_booster.threads.media_delivery import ThreadsMediaDelivery


class ThreadsPublicationError(ValueError):
    pass


def publication_operation_id(invocation_sha256: str, item_id: str) -> str:
    digest = sha256(f"{invocation_sha256}\n{item_id}".encode()).hexdigest()[:24]
    return "threads-operation-" + digest


_OPTIONAL_ROW: TypeAdapter[tuple[str, int] | None] = TypeAdapter(tuple[str, int] | None)
_RECEIPT_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_TABLE_INFO: TypeAdapter[list[tuple[int, str, str, int, str | None, int]]] = TypeAdapter(
    list[tuple[int, str, str, int, str | None, int]]
)


@dataclass(frozen=True, slots=True)
class ThreadsPublicationRepository:
    database_path: Path

    def __post_init__(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.executescript(
                """
                CREATE TABLE IF NOT EXISTS threads_publications (
                    operation_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    draft_revision INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    run_settled INTEGER NOT NULL DEFAULT 0,
                    sequence INTEGER NOT NULL,
                    receipt_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS threads_publication_events (
                    operation_id TEXT NOT NULL REFERENCES threads_publications(operation_id),
                    sequence INTEGER NOT NULL,
                    receipt_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(operation_id, sequence)
                );
                CREATE TRIGGER IF NOT EXISTS threads_publication_events_immutable
                BEFORE UPDATE ON threads_publication_events BEGIN
                    SELECT RAISE(ABORT, 'threads publication events are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS threads_publication_events_append_only
                BEFORE DELETE ON threads_publication_events BEGIN
                    SELECT RAISE(ABORT, 'threads publication events are append-only');
                END;
                """
            )
            columns = {
                row[1]
                for row in _TABLE_INFO.validate_python(
                    database.execute("PRAGMA table_info(threads_publications)").fetchall()
                )
            }
            if "batch_id" not in columns:
                _ = database.execute(
                    "ALTER TABLE threads_publications ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''"
                )
            if "item_id" not in columns:
                _ = database.execute(
                    "ALTER TABLE threads_publications ADD COLUMN item_id TEXT NOT NULL DEFAULT ''"
                )
            if "draft_revision" not in columns:
                _ = database.execute(
                    """ALTER TABLE threads_publications ADD COLUMN
                    draft_revision INTEGER NOT NULL DEFAULT 0"""
                )
            if "run_settled" not in columns:
                _ = database.execute(
                    """ALTER TABLE threads_publications ADD COLUMN
                    run_settled INTEGER NOT NULL DEFAULT 0"""
                )
            _ = database.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS threads_publication_draft_item
                ON threads_publications(batch_id,item_id,draft_revision)
                WHERE batch_id<>''"""
            )

    def get(self, operation_id: str) -> ThreadsPublicationReceipt | None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    "SELECT receipt_json,sequence FROM threads_publications WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
            )
        if row is None:
            return None
        return ThreadsPublicationReceipt.model_validate_json(row[0])

    def get_for_item(
        self, batch_id: str, item_id: str, draft_revision: int
    ) -> ThreadsPublicationReceipt | None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    """SELECT receipt_json,sequence FROM threads_publications
                    WHERE batch_id=? AND item_id=? AND draft_revision=?""",
                    (batch_id, item_id, draft_revision),
                ).fetchone()
            )
        return None if row is None else ThreadsPublicationReceipt.model_validate_json(row[0])

    def list_for_batch(
        self, batch_id: str, *, draft_revision: int | None = None
    ) -> tuple[ThreadsPublicationReceipt, ...]:
        query = "SELECT receipt_json FROM threads_publications WHERE batch_id=?"
        values: tuple[str | int, ...] = (batch_id,)
        if draft_revision is not None:
            query += " AND draft_revision=?"
            values = (batch_id, draft_revision)
        query += " ORDER BY item_id,operation_id"
        with closing(sqlite3.connect(self.database_path)) as database, database:
            rows = _RECEIPT_ROWS.validate_python(database.execute(query, values).fetchall())
        return tuple(ThreadsPublicationReceipt.model_validate_json(row[0]) for row in rows)

    def reconciliation_candidates(
        self, *, limit: int = 100
    ) -> tuple[ThreadsPublicationReceipt, ...]:
        if limit < 1 or limit > 1000:
            raise ThreadsPublicationError("threads_publication_limit_invalid")
        with closing(sqlite3.connect(self.database_path)) as database, database:
            rows = _RECEIPT_ROWS.validate_python(
                database.execute(
                    """SELECT receipt_json FROM threads_publications
                    WHERE state IN ('uncertain','published')
                    AND run_settled=0
                    ORDER BY updated_at DESC,operation_id LIMIT ?""",
                    (limit,),
                ).fetchall()
            )
        return tuple(ThreadsPublicationReceipt.model_validate_json(row[0]) for row in rows)

    def for_invocation(self, invocation_sha256: str) -> tuple[ThreadsPublicationReceipt, ...]:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            rows = _RECEIPT_ROWS.validate_python(
                database.execute(
                    """SELECT receipt_json FROM threads_publications
                    WHERE json_extract(receipt_json,'$.invocation_sha256')=?
                    ORDER BY item_id,operation_id""",
                    (invocation_sha256,),
                ).fetchall()
            )
        return tuple(ThreadsPublicationReceipt.model_validate_json(row[0]) for row in rows)

    def mark_run_settled(self, operation_id: str) -> None:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.execute(
                "UPDATE threads_publications SET run_settled=1 WHERE operation_id=?",
                (operation_id,),
            )

    def put(self, receipt: ThreadsPublicationReceipt) -> ThreadsPublicationReceipt:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            _ = database.execute("BEGIN IMMEDIATE")
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    "SELECT receipt_json,sequence FROM threads_publications WHERE operation_id=?",
                    (receipt.operation_id,),
                ).fetchone()
            )
            if row is None:
                sequence = 1
                _ = database.execute(
                    """INSERT INTO threads_publications(
                    operation_id,batch_id,item_id,draft_revision,state,sequence,
                    receipt_json,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        receipt.operation_id,
                        receipt.batch_id,
                        receipt.item_id,
                        receipt.draft_revision,
                        receipt.state,
                        sequence,
                        receipt.model_dump_json(),
                        receipt.updated_at.isoformat(),
                    ),
                )
            else:
                current = ThreadsPublicationReceipt.model_validate_json(row[0])
                if current == receipt:
                    return current
                reconciling_uncertain = (
                    current.state == "uncertain"
                    and receipt.state == "published"
                    and current.model_copy(
                        update={
                            "state": "published",
                            "permalink": receipt.permalink,
                            "pending_step": receipt.pending_step,
                            "error_code": receipt.error_code,
                            "provider_status": receipt.provider_status,
                            "provider_error_code": receipt.provider_error_code,
                            "retry_after_seconds": receipt.retry_after_seconds,
                            "updated_at": receipt.updated_at,
                        }
                    )
                    == receipt
                )
                if (
                    current.state in {"published", "failed", "uncertain"}
                    and not reconciling_uncertain
                ):
                    raise ThreadsPublicationError("threads_publication_terminal")
                sequence = row[1] + 1
                _ = database.execute(
                    """UPDATE threads_publications SET state=?,sequence=?,receipt_json=?,updated_at=?
                    WHERE operation_id=? AND sequence=?""",
                    (
                        receipt.state,
                        sequence,
                        receipt.model_dump_json(),
                        receipt.updated_at.isoformat(),
                        receipt.operation_id,
                        row[1],
                    ),
                )
            _ = database.execute(
                """INSERT INTO threads_publication_events(
                operation_id,sequence,receipt_json,recorded_at
                ) VALUES(?,?,?,?)""",
                (
                    receipt.operation_id,
                    sequence,
                    receipt.model_dump_json(),
                    receipt.updated_at.isoformat(),
                ),
            )
        return receipt


@dataclass(slots=True)
class ThreadsPublisher:
    api: ThreadsApiClient
    accounts: ThreadsAccountRepository
    tokens: ThreadsTokenVault
    drafts: ThreadsDraftRepository
    media: ThreadsMediaDelivery
    publications: ThreadsPublicationRepository

    def reconcile(
        self,
        *,
        operation_id: str,
        workspace_id: str,
        member_id: str,
        now: datetime,
    ) -> ThreadsPublicationReceipt:
        receipt = self.publications.get(operation_id)
        if receipt is None:
            raise ThreadsPublicationError("threads_publication_not_found")
        if receipt.workspace_id != workspace_id or receipt.owner_member_id != member_id:
            raise ThreadsPublicationError("threads_publication_owner_required")
        if receipt.state == "published":
            return receipt
        account = self.accounts.require_owner(workspace_id, receipt.connection_id, member_id)
        if receipt.published_post_id is None or receipt.state != "uncertain":
            return receipt
        token = self.tokens.get(account.token_ref)
        try:
            post = self.api.post(
                token,
                receipt.published_post_id,
                account_id=account.connection_id,
            )
        except ThreadsApiError as error:
            if error.status == 401:
                _ = self.accounts.mark_reauth(account, now=now)
            return receipt
        if post.post_id != receipt.published_post_id or not post.permalink:
            return receipt
        return self.publications.put(
            receipt.model_copy(
                update={
                    "state": "published",
                    "permalink": post.permalink,
                    "pending_step": None,
                    "error_code": None,
                    "provider_status": None,
                    "provider_error_code": None,
                    "retry_after_seconds": None,
                    "updated_at": now,
                }
            )
        )

    def publish(
        self,
        *,
        operation_id: str,
        workspace_id: str,
        member_id: str,
        batch_id: str,
        batch_revision: int,
        item_id: str,
        expected_action: ThreadsDraftAction,
        run_id: str,
        invocation_sha256: str,
        now: datetime,
    ) -> ThreadsPublicationReceipt:
        existing = self.publications.get(operation_id)
        if existing is not None:
            if existing.state in {"published", "failed", "uncertain"}:
                return existing
            if existing.pending_step is not None:
                return self.publications.put(
                    existing.model_copy(
                        update={
                            "state": "uncertain",
                            "error_code": "threads_publication_interrupted",
                            "updated_at": now,
                        }
                    )
                )
        dispatched = self.publications.get_for_item(batch_id, item_id, batch_revision)
        if dispatched is not None and dispatched.operation_id != operation_id:
            raise ThreadsPublicationError("threads_draft_item_already_dispatched")
        batch = self.drafts.get(workspace_id, batch_id)
        if (
            batch is None
            or batch.revision != batch_revision
            or batch.state not in {ThreadsDraftState.DRAFT, ThreadsDraftState.APPROVED}
            or batch.owner_member_id != member_id
        ):
            raise ThreadsPublicationError("threads_current_draft_required")
        item = next((candidate for candidate in batch.items if candidate.item_id == item_id), None)
        if item is None or item.excluded or item.action is not expected_action:
            raise ThreadsPublicationError("threads_draft_item_unavailable")
        account = self.accounts.require_owner(workspace_id, item.connection_id, member_id)
        _ = self.accounts.require_readable(workspace_id, item.connection_id, now=now)
        if "threads_content_publish" not in account.granted_scopes:
            raise ThreadsPublicationError("threads_publish_scope_unavailable")
        token = self.tokens.get(account.token_ref)
        if existing is None:
            receipt = self.publications.put(
                ThreadsPublicationReceipt(
                    operation_id=operation_id,
                    workspace_id=workspace_id,
                    owner_member_id=member_id,
                    run_id=run_id,
                    invocation_sha256=invocation_sha256,
                    connection_id=account.connection_id,
                    batch_id=batch.batch_id,
                    item_id=item.item_id,
                    draft_revision=batch.revision,
                    ordered_asset_sha256=tuple(asset.sha256 for asset in item.assets),
                    reply_to_id=item.reply_to_id,
                    state="prepared",
                    updated_at=now,
                )
            )
        else:
            receipt = existing
            if (
                receipt.connection_id != account.connection_id
                or receipt.workspace_id != workspace_id
                or receipt.owner_member_id != member_id
                or receipt.run_id != run_id
                or receipt.invocation_sha256 != invocation_sha256
                or receipt.draft_revision != batch.revision
                or receipt.ordered_asset_sha256 != tuple(asset.sha256 for asset in item.assets)
                or receipt.reply_to_id != item.reply_to_id
            ):
                raise ThreadsPublicationError("threads_publication_identity_conflict")
        try:
            receipt = self._create(token, batch, item_id, receipt=receipt, now=now)
            if receipt.published_post_id is None:
                self.api.wait_until_ready(token, creation_id=receipt.creation_ids[-1])
                receipt = self.publications.put(
                    receipt.model_copy(update={"pending_step": "publish", "updated_at": now})
                )
                published_id = self.api.publish(token, creation_id=receipt.creation_ids[-1])
                receipt = self.publications.put(
                    receipt.model_copy(
                        update={
                            "state": "publishing",
                            "published_post_id": published_id,
                            "pending_step": None,
                            "updated_at": now,
                        }
                    )
                )
            published_post_id = receipt.published_post_id
            if published_post_id is None:
                raise ThreadsPublicationError("threads_published_id_missing")
            readback = self.api.post(
                token,
                published_post_id,
                account_id=account.connection_id,
            )
        except ThreadsApiError as error:
            if error.status == 401:
                _ = self.accounts.mark_reauth(account, now=now)
            state = (
                "uncertain"
                if error.uncertain_effect or receipt.published_post_id is not None
                else "failed"
            )
            return self.publications.put(
                receipt.model_copy(
                    update={
                        "state": state,
                        "error_code": "threads_api_request_failed",
                        "provider_status": error.status,
                        "provider_error_code": error.code,
                        "retry_after_seconds": error.retry_after_seconds,
                        "updated_at": now,
                    }
                )
            )
        if readback.post_id != receipt.published_post_id or not readback.permalink:
            return self.publications.put(
                receipt.model_copy(
                    update={
                        "state": "uncertain",
                        "error_code": "threads_publish_readback_mismatch",
                        "updated_at": now,
                    }
                )
            )
        return self.publications.put(
            receipt.model_copy(
                update={
                    "state": "published",
                    "permalink": readback.permalink,
                    "error_code": None,
                    "provider_status": None,
                    "provider_error_code": None,
                    "retry_after_seconds": None,
                    "updated_at": now,
                }
            )
        )

    def _create(
        self,
        token: str,
        batch: ThreadsDraftBatch,
        item_id: str,
        *,
        receipt: ThreadsPublicationReceipt,
        now: datetime,
    ) -> ThreadsPublicationReceipt:
        current = self.drafts.get(batch.workspace_id, batch.batch_id)
        if current is None:
            raise ThreadsPublicationError("threads_draft_not_found")
        item = next(candidate for candidate in current.items if candidate.item_id == item_id)
        if not item.assets:
            if receipt.creation_ids:
                return receipt
            receipt = self.publications.put(
                receipt.model_copy(update={"pending_step": "create_text", "updated_at": now})
            )
            creation = self.api.create_text(
                token,
                text=item.text,
                reply_to_id=item.reply_to_id if item.action is ThreadsDraftAction.REPLY else None,
            )
            return self.publications.put(
                receipt.model_copy(
                    update={
                        "state": "publishing",
                        "creation_ids": (creation,),
                        "pending_step": None,
                        "updated_at": now,
                    }
                )
            )
        grants = self.media.issue(
            current, item_id=item.item_id, expires_at=now + timedelta(minutes=30), now=now
        )
        child_count = len(grants)
        known = list(receipt.creation_ids)
        if len(known) > child_count + 1:
            raise ThreadsPublicationError("threads_creation_identity_invalid")
        while len(known) < child_count:
            index = len(known)
            receipt = self.publications.put(
                receipt.model_copy(
                    update={"pending_step": f"create_child:{index}", "updated_at": now}
                )
            )
            grant = grants[index]
            creation = self.api.create_image_container(
                token, image_url=grant.url, alt_text=grant.asset.alt_text
            )
            known.append(creation)
            receipt = self.publications.put(
                receipt.model_copy(
                    update={
                        "creation_ids": tuple(known),
                        "pending_step": None,
                        "updated_at": now,
                    }
                )
            )
            self.api.wait_until_ready(token, creation_id=creation)
        for child_id in known[:child_count]:
            self.api.wait_until_ready(token, creation_id=child_id)
        if len(known) == child_count:
            receipt = self.publications.put(
                receipt.model_copy(update={"pending_step": "create_carousel", "updated_at": now})
            )
            parent = self.api.create_carousel(
                token,
                children=tuple(known),
                text=item.text,
                reply_to_id=item.reply_to_id if item.action is ThreadsDraftAction.REPLY else None,
            )
            known.append(parent)
            receipt = self.publications.put(
                receipt.model_copy(
                    update={
                        "state": "publishing",
                        "creation_ids": tuple(known),
                        "pending_step": None,
                        "updated_at": now,
                    }
                )
            )
        return receipt


__all__ = [
    "ThreadsPublicationError",
    "ThreadsPublicationRepository",
    "ThreadsPublisher",
    "publication_operation_id",
]

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, unique
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.models import ContractModel, CountryCode, Identifier, Sha256Digest
from ads_booster.contracts.threads import ThreadsProviderId


@unique
class ThreadsDraftAction(StrEnum):
    PUBLISH = "publish"
    REPLY = "reply"


@unique
class ThreadsDraftState(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXECUTING = "executing"
    PARTIAL = "partial"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ThreadsAssetReference(ContractModel):
    asset_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    sha256: Sha256Digest
    alt_text: Annotated[str, Field(min_length=1, max_length=1_000)]


class ThreadsDraftItem(ContractModel):
    item_id: Identifier
    action: ThreadsDraftAction
    connection_id: Identifier
    country: CountryCode | None = None
    text: Annotated[str, Field(min_length=1, max_length=500)]
    reply_to_id: ThreadsProviderId | None = None
    assets: Annotated[tuple[ThreadsAssetReference, ...], Field(max_length=20)] = ()
    excluded: bool = False

    @model_validator(mode="after")
    def require_action_shape(self) -> Self:
        if self.action is ThreadsDraftAction.REPLY and self.reply_to_id is None:
            raise PydanticCustomError("threads_reply_target_required", "reply target is required")
        if self.action is ThreadsDraftAction.PUBLISH and self.reply_to_id is not None:
            raise PydanticCustomError(
                "threads_publish_target_forbidden", "publish draft cannot have reply target"
            )
        if len(self.assets) == 1:
            raise PydanticCustomError(
                "threads_carousel_requires_pair", "a carousel needs at least two assets"
            )
        return self


class ThreadsDraftBatch(ContractModel):
    schema_version: Literal["trace.threads-draft-batch.v1"] = "trace.threads-draft-batch.v1"
    batch_id: Identifier
    workspace_id: Identifier
    owner_member_id: Identifier
    conversation_id: Identifier
    source_event_id: Identifier
    items: Annotated[tuple[ThreadsDraftItem, ...], Field(min_length=1, max_length=50)]
    state: ThreadsDraftState = ThreadsDraftState.DRAFT
    revision: Annotated[int, Field(ge=1)] = 1
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_batch_identity(self) -> Self:
        if len({item.item_id for item in self.items}) != len(self.items):
            raise PydanticCustomError(
                "threads_draft_item_duplicate", "draft item ids must be unique"
            )
        if sum(len(item.assets) for item in self.items if not item.excluded) > 20:
            raise PydanticCustomError(
                "threads_draft_asset_limit", "a draft batch may expose at most 20 assets"
            )
        if sum(len(item.text) for item in self.items) > 40_000:
            raise PydanticCustomError(
                "threads_draft_text_limit", "a draft batch may contain at most 40000 characters"
            )
        for value in (self.created_at, self.updated_at):
            if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
                raise PydanticCustomError("threads_draft_time_requires_utc", "datetime must be UTC")
        if self.updated_at < self.created_at:
            raise PydanticCustomError(
                "threads_draft_update_time_invalid", "updated_at must not precede created_at"
            )
        return self


class ThreadsDraftConflictError(ValueError):
    pass


_OPTIONAL_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


@dataclass(frozen=True, slots=True)
class ThreadsDraftRepository:
    database_path: Path

    def __post_init__(self) -> None:
        with sqlite3.connect(self.database_path) as database:
            _ = database.executescript(
                """
                CREATE TABLE IF NOT EXISTS threads_draft_batches (
                    batch_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    owner_member_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    batch_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS threads_draft_revisions (
                    batch_id TEXT NOT NULL REFERENCES threads_draft_batches(batch_id) ON DELETE RESTRICT,
                    revision INTEGER NOT NULL,
                    batch_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(batch_id, revision)
                );
                CREATE TRIGGER IF NOT EXISTS threads_draft_revisions_immutable
                BEFORE UPDATE ON threads_draft_revisions BEGIN
                    SELECT RAISE(ABORT, 'threads draft revisions are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS threads_draft_revisions_append_only
                BEFORE DELETE ON threads_draft_revisions BEGIN
                    SELECT RAISE(ABORT, 'threads draft revisions are append-only');
                END;
                """
            )

    def create(self, batch: ThreadsDraftBatch) -> ThreadsDraftBatch:
        try:
            with sqlite3.connect(self.database_path) as database:
                _ = database.execute("BEGIN IMMEDIATE")
                _ = database.execute(
                    """INSERT INTO threads_draft_batches(
                    batch_id,workspace_id,owner_member_id,revision,state,batch_json,updated_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        batch.batch_id,
                        batch.workspace_id,
                        batch.owner_member_id,
                        batch.revision,
                        batch.state,
                        batch.model_dump_json(),
                        batch.updated_at.isoformat(),
                    ),
                )
                self._append(database, batch)
        except sqlite3.IntegrityError as error:
            current = self.get(batch.workspace_id, batch.batch_id)
            if current is not None and current == batch:
                return current
            raise ThreadsDraftConflictError("threads_draft_id_conflict") from error
        return batch

    def get(self, workspace_id: str, batch_id: str) -> ThreadsDraftBatch | None:
        with sqlite3.connect(self.database_path) as database:
            row = _OPTIONAL_ROW.validate_python(
                database.execute(
                    """SELECT batch_json FROM threads_draft_batches
                    WHERE workspace_id=? AND batch_id=?""",
                    (workspace_id, batch_id),
                ).fetchone()
            )
        if row is None:
            return None
        return ThreadsDraftBatch.model_validate_json(row[0])

    def replace(
        self,
        batch: ThreadsDraftBatch,
        *,
        expected_revision: int,
        actor_id: str,
    ) -> ThreadsDraftBatch:
        with sqlite3.connect(self.database_path) as database:
            _ = database.execute("BEGIN IMMEDIATE")
            current = self._required(database, batch.workspace_id, batch.batch_id)
            if current.owner_member_id != actor_id:
                raise ThreadsDraftConflictError("threads_draft_owner_required")
            if current.revision != expected_revision or batch.revision != expected_revision + 1:
                raise ThreadsDraftConflictError("threads_draft_revision_conflict")
            if (
                batch.created_at != current.created_at
                or batch.owner_member_id != current.owner_member_id
                or batch.source_event_id != current.source_event_id
            ):
                raise ThreadsDraftConflictError("threads_draft_identity_changed")
            changed = database.execute(
                """UPDATE threads_draft_batches SET revision=?,state=?,batch_json=?,updated_at=?
                WHERE batch_id=? AND workspace_id=? AND revision=?""",
                (
                    batch.revision,
                    batch.state,
                    batch.model_dump_json(),
                    batch.updated_at.isoformat(),
                    batch.batch_id,
                    batch.workspace_id,
                    expected_revision,
                ),
            ).rowcount
            if changed != 1:
                raise ThreadsDraftConflictError("threads_draft_revision_conflict")
            self._append(database, batch)
        return batch

    def _required(
        self, database: sqlite3.Connection, workspace_id: str, batch_id: str
    ) -> ThreadsDraftBatch:
        row = _OPTIONAL_ROW.validate_python(
            database.execute(
                """SELECT batch_json FROM threads_draft_batches
                WHERE workspace_id=? AND batch_id=?""",
                (workspace_id, batch_id),
            ).fetchone()
        )
        if row is None:
            raise ThreadsDraftConflictError("threads_draft_not_found")
        return ThreadsDraftBatch.model_validate_json(row[0])

    @staticmethod
    def _append(database: sqlite3.Connection, batch: ThreadsDraftBatch) -> None:
        _ = database.execute(
            """INSERT INTO threads_draft_revisions(
            batch_id,revision,batch_json,recorded_at
            ) VALUES(?,?,?,?)""",
            (
                batch.batch_id,
                batch.revision,
                batch.model_dump_json(),
                batch.updated_at.isoformat(),
            ),
        )


__all__ = [
    "ThreadsAssetReference",
    "ThreadsDraftAction",
    "ThreadsDraftBatch",
    "ThreadsDraftConflictError",
    "ThreadsDraftItem",
    "ThreadsDraftRepository",
    "ThreadsDraftState",
]

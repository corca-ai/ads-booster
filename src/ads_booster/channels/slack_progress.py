"""Durable status-message identity and cancellation, separate from Run authority."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.channels.slack_conversations import Message

if TYPE_CHECKING:
    from ads_booster.channels.slack_conversations import SlackConversationStore

_MESSAGE_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_COLUMNS = TypeAdapter(list[tuple[int, str, str, int, str | None, int]])

_ROW: TypeAdapter[tuple[str, str, str, str, int, float] | None] = TypeAdapter(
    tuple[str, str, str, str, int, float] | None
)


@dataclass(frozen=True, slots=True)
class ProgressRecord:
    message_id: str
    conversation_id: str
    user_id: str
    run_id: str
    channel_id: str
    timestamp: str
    cancelled: bool
    started_at: float = 0.0


@dataclass(slots=True)
class SlackProgressStore:
    inbox: SlackConversationStore

    def __post_init__(self) -> None:
        """Add status identity without rewriting the inbox or Run history."""
        with self.inbox.connect() as db:
            _ = db.execute("""CREATE TABLE IF NOT EXISTS slack_progress (
                message_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                channel_id TEXT NOT NULL, timestamp TEXT NOT NULL DEFAULT '',
                cancelled INTEGER NOT NULL DEFAULT 0)""")
            columns = {
                row[1]
                for row in _COLUMNS.validate_python(
                    db.execute("PRAGMA table_info(slack_progress)").fetchall()
                )
            }
            if "started_at" not in columns:
                _ = db.execute(
                    "ALTER TABLE slack_progress ADD COLUMN started_at REAL NOT NULL DEFAULT 0"
                )

    def begin(self, message_id: str, run_id: str, channel_id: str) -> None:
        with self.inbox.connect() as db:
            _ = db.execute(
                """INSERT OR IGNORE INTO slack_progress (message_id,run_id,channel_id)
                VALUES (?,?,?)""",
                (message_id, run_id, channel_id),
            )
            _ = db.execute(
                "UPDATE slack_progress SET started_at=? WHERE message_id=? AND started_at=0",
                (time.time(), message_id),
            )

    def locate(self, message_id: str) -> ProgressRecord | None:
        with self.inbox.connect() as db:
            row = _ROW.validate_python(
                db.execute(
                    """SELECT p.run_id,p.channel_id,p.timestamp,j.message_json,
                    p.cancelled,p.started_at
                FROM slack_progress p JOIN slack_message_jobs j USING(message_id)
                WHERE message_id=?""",
                    (message_id,),
                ).fetchone()
            )
        if row is None:
            return None
        message = Message.model_validate_json(row[3])
        return ProgressRecord(
            message_id,
            message.conversation_id,
            message.user_id,
            row[0],
            row[1],
            row[2],
            bool(row[4]),
            row[5],
        )

    def elapsed_seconds(self, message_id: str) -> int:
        record = self.locate(message_id)
        return max(0, int(time.time() - record.started_at)) if record and record.started_at else 0

    def sent(self, message_id: str, timestamp: str) -> None:
        with self.inbox.connect() as db:
            _ = db.execute(
                "UPDATE slack_progress SET timestamp=? WHERE message_id=? AND timestamp=''",
                (timestamp, message_id),
            )

    def cancel(self, message_id: str) -> None:
        with self.inbox.connect() as db:
            _ = db.execute(
                """UPDATE slack_progress SET cancelled=1 WHERE message_id=?
                AND EXISTS (SELECT 1 FROM slack_message_jobs j
                WHERE j.message_id=slack_progress.message_id
                AND (j.state IN ('running','pending') OR EXISTS (
                    SELECT 1 FROM agent_drive_work w
                    JOIN agent_drive_origins o USING(tenant_id,run_id)
                    WHERE w.run_id=slack_progress.run_id
                    AND json_extract(o.origin_json,'$.event_id')=j.message_id
                    AND w.state IN ('running','pending'))))""",
                (message_id,),
            )

    def cancelled(self, message_id: str) -> bool:
        record = self.locate(message_id)
        return record is not None and record.cancelled

    def for_run(self, conversation_id: str, run_id: str) -> ProgressRecord | None:
        with self.inbox.connect() as db:
            row = _MESSAGE_ROW.validate_python(
                db.execute(
                    """SELECT p.message_id FROM slack_progress p
                JOIN slack_message_jobs j USING(message_id)
                WHERE j.conversation_id=? AND p.run_id=? AND p.timestamp<>''
                ORDER BY j.rowid DESC LIMIT 1""",
                    (conversation_id, run_id),
                ).fetchone()
            )
        return None if row is None else self.locate(row[0])

"""Durable status-message identity and cancellation, separate from Run authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.channels.slack_conversations import Message

if TYPE_CHECKING:
    from ads_booster.channels.slack_conversations import SlackConversationStore

_ROW: TypeAdapter[tuple[str, str, str, str, int] | None] = TypeAdapter(
    tuple[str, str, str, str, int] | None
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

    def begin(self, message_id: str, run_id: str, channel_id: str) -> None:
        with self.inbox.connect() as db:
            _ = db.execute(
                """INSERT OR IGNORE INTO slack_progress (message_id,run_id,channel_id)
                VALUES (?,?,?)""",
                (message_id, run_id, channel_id),
            )

    def locate(self, message_id: str) -> ProgressRecord | None:
        with self.inbox.connect() as db:
            row = _ROW.validate_python(
                db.execute(
                    """SELECT p.run_id,p.channel_id,p.timestamp,j.message_json,p.cancelled
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
        )

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
                AND j.state IN ('running','pending'))""",
                (message_id,),
            )

    def cancelled(self, message_id: str) -> bool:
        record = self.locate(message_id)
        return record is not None and record.cancelled

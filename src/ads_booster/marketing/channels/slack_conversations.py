"""Durable Slack conversation routing and inbox beside the canonical Run ledger."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import AgentGoal
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.channels.slack_attachments import SlackAttachment
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


class Conversation(ContractModel):
    conversation_id: str
    tenant_id: str
    channel_id: str
    thread_ts: str
    owner_id: str
    private: bool
    current_run: str = ""
    closed: bool = False


class Message(ContractModel):
    message_id: str
    conversation_id: str
    user_id: str
    text: str
    reopens: bool = False
    attachments: tuple[SlackAttachment, ...] = ()


class MessagePlan(ContractModel):
    action: Literal[
        "create",
        "input",
        "approve",
        "reject",
        "resume",
        "reply",
        "close",
        "revise",
        "pause",
        "memory",
        "delivery",
        "observation",
    ]
    run_id: str = ""
    goal: AgentGoal | None = None
    evidence: JsonObject | None = None
    revision: int = -1
    digest: str = ""
    reply: str = ""


_ROW: TypeAdapter[tuple[str, ...] | None] = TypeAdapter(tuple[str, ...] | None)
_ROWS = TypeAdapter(list[tuple[str, ...]])
_MAX_PENDING = 1000
_MAX_CONTEXT_CHARS = 24000


class SlackInboxFullError(RuntimeError):
    """Retryable admission failure; no delivery was accepted."""


@dataclass(frozen=True, slots=True)
class SlackConversationStore:
    database_path: Path

    def __post_init__(self) -> None:
        """Create the durable inbox without modifying canonical Run records."""
        with self.connect() as db:
            _ = db.executescript("""
                CREATE TABLE IF NOT EXISTS slack_conversations (
                    conversation_id TEXT PRIMARY KEY, data_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS slack_message_jobs (
                    message_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    message_json TEXT NOT NULL, plan_json TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'pending', result TEXT NOT NULL DEFAULT '',
                    notification_state TEXT NOT NULL DEFAULT 'pending',
                    ack_state TEXT NOT NULL DEFAULT 'pending');
                CREATE INDEX IF NOT EXISTS slack_message_scope
                    ON slack_message_jobs(conversation_id);
            """)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=1)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def conversation(self, conversation_id: str) -> Conversation | None:
        with self.connect() as db:
            row = _ROW.validate_python(
                db.execute(
                    "SELECT data_json FROM slack_conversations WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()
            )
        return None if row is None else Conversation.model_validate_json(row[0])

    def admit(self, conversation: Conversation, message: Message) -> None:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = _ROW.validate_python(
                db.execute(
                    "SELECT message_json FROM slack_message_jobs WHERE message_id=?",
                    (message.message_id,),
                ).fetchone()
            )
            if row is not None:
                if Message.model_validate_json(row[0]) != message:
                    raise ValueError("slack_message_idempotency_conflict")
                return
            count = TypeAdapter(int).validate_python(
                db.execute(
                    "SELECT COUNT(*) FROM slack_message_jobs WHERE state IN ('pending','running')"
                ).fetchone()[0]
            )
            if count >= _MAX_PENDING:
                raise SlackInboxFullError("slack_inbox_full")
            _ = db.execute(
                "INSERT OR IGNORE INTO slack_conversations VALUES (?,?)",
                (conversation.conversation_id, conversation.model_dump_json()),
            )
            _ = db.execute(
                """INSERT INTO slack_message_jobs
                (message_id,conversation_id,message_json) VALUES (?,?,?)""",
                (message.message_id, conversation.conversation_id, message.model_dump_json()),
            )

    def update_conversation(self, conversation: Conversation) -> None:
        with self.connect() as db:
            _ = db.execute(
                "UPDATE slack_conversations SET data_json=? WHERE conversation_id=?",
                (conversation.model_dump_json(), conversation.conversation_id),
            )

    def conversation_for_run(self, tenant_id: str, run_id: str) -> Conversation | None:
        with self.connect() as db:
            row = _ROW.validate_python(
                db.execute(
                    """SELECT data_json FROM slack_conversations
                WHERE json_extract(data_json,'$.tenant_id')=?
                AND json_extract(data_json,'$.current_run')=? LIMIT 1""",
                    (tenant_id, run_id),
                ).fetchone()
            )
        return None if row is None else Conversation.model_validate_json(row[0])

    def pending_for_run(self, tenant_id: str, run_id: str) -> tuple[Message, ...]:
        with self.connect() as db:
            rows = _ROWS.validate_python(
                db.execute(
                    """SELECT j.message_json FROM slack_message_jobs j
                JOIN slack_conversations c ON c.conversation_id=j.conversation_id
                WHERE j.state='pending' AND json_extract(c.data_json,'$.tenant_id')=?
                AND json_extract(c.data_json,'$.current_run')=? ORDER BY j.rowid LIMIT 1000""",
                    (tenant_id, run_id),
                ).fetchall()
            )
        return tuple(Message.model_validate_json(row[0]) for row in rows)

    def claim(self) -> tuple[Message, MessagePlan | None] | None:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = _ROW.validate_python(
                db.execute("""SELECT message_json,plan_json FROM
                slack_message_jobs WHERE state='pending' ORDER BY rowid LIMIT 1""").fetchone()
            )
            if row is None:
                return None
            message = Message.model_validate_json(row[0])
            _ = db.execute(
                "UPDATE slack_message_jobs SET state='running' WHERE message_id=?",
                (message.message_id,),
            )
        return message, MessagePlan.model_validate_json(row[1]) if row[1] else None

    def save_plan(self, message: Message, plan: MessagePlan) -> None:
        with self.connect() as db:
            _ = db.execute(
                "UPDATE slack_message_jobs SET plan_json=? WHERE message_id=?",
                (plan.model_dump_json(), message.message_id),
            )

    def finish(self, message: Message, result: str, *, blocked: bool = False) -> None:
        with self.connect() as db:
            _ = db.execute(
                "UPDATE slack_message_jobs SET state=?,result=? WHERE message_id=?",
                ("blocked" if blocked else "done", result, message.message_id),
            )

    def recover(self) -> None:
        with self.connect() as db:
            for message_id, raw in _ROWS.validate_python(
                db.execute(
                    "SELECT message_id,plan_json FROM slack_message_jobs WHERE state='running'"
                ).fetchall()
            ):
                plan = MessagePlan.model_validate_json(raw) if raw else None
                replayable = plan is None or plan.action in {
                    "create",
                    "reply",
                    "close",
                    "revise",
                    "pause",
                    "observation",
                }
                _ = db.execute(
                    "UPDATE slack_message_jobs SET state=?,result=? WHERE message_id=?",
                    (
                        "pending" if replayable else "blocked",
                        "재시작으로 처리를 멈췄습니다. 대화에 '상태'를 보내 확인하세요.",
                        message_id,
                    ),
                )
            _ = db.execute("""UPDATE slack_message_jobs SET notification_state='unknown'
                WHERE notification_state='sending'""")
            _ = db.execute(
                "UPDATE slack_message_jobs SET ack_state='unknown' WHERE ack_state='sending'"
            )

    def transcript(self, conversation_id: str) -> JsonObject:
        with self.connect() as db:
            rows = _ROWS.validate_python(
                db.execute(
                    """SELECT message_json,result FROM
                slack_message_jobs WHERE conversation_id=? AND state IN ('done','blocked')
                ORDER BY rowid DESC LIMIT 21""",
                    (conversation_id,),
                ).fetchall()
            )
        messages: list[JsonObject] = []
        size = 0
        for raw, reply in rows[:20]:
            message = Message.model_validate_json(raw)
            if size + len(message.text) + len(reply) > _MAX_CONTEXT_CHARS:
                break
            size += len(message.text) + len(reply)
            messages.append(
                {
                    "user_id": message.user_id,
                    "user": message.text,
                    "assistant": reply,
                    "attachments": [a.model_dump(mode="json") for a in message.attachments],
                }
            )
        return {
            "messages": list(reversed(messages)),
            "projection_truncated": len(messages) < len(rows),
        }

    def claim_notification(self) -> tuple[Message, str] | None:
        with self.connect() as db:
            _ = db.execute("BEGIN IMMEDIATE")
            row = _ROW.validate_python(
                db.execute("""SELECT message_json,result FROM
                slack_message_jobs WHERE state IN ('done','blocked')
                AND notification_state='pending' ORDER BY rowid LIMIT 1""").fetchone()
            )
            if row is None:
                return None
            message = Message.model_validate_json(row[0])
            _ = db.execute(
                "UPDATE slack_message_jobs SET notification_state='sending' WHERE message_id=?",
                (message.message_id,),
            )
            return message, row[1]

    def claim_ack(self, message: Message) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE slack_message_jobs SET ack_state='sending'
                WHERE message_id=? AND ack_state='pending'""",
                (message.message_id,),
            )
            return cursor.rowcount == 1

    def sent(self, message: Message, state: str, *, ack: bool = False) -> None:
        column = "ack_state" if ack else "notification_state"
        with self.connect() as db:
            _ = db.execute(
                f"UPDATE slack_message_jobs SET {column}=? WHERE message_id=?",  # noqa: S608 - fixed internal column.
                (state, message.message_id),
            )

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.channels.task_results import DeliveryIdentity, TaskResult
from ads_booster.contracts.agent_run import contract_sha256

if TYPE_CHECKING:
    from pathlib import Path
    from sqlite3 import Connection

    from ads_booster.transport.json_types import JsonObject

_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_ROWS: TypeAdapter[list[tuple[str, str]]] = TypeAdapter(list[tuple[str, str]])


def ensure_bindings(connection: Connection) -> None:
    _ = connection.execute("""CREATE TABLE IF NOT EXISTS task_result_deliveries (
        notification_id TEXT PRIMARY KEY, identity_json TEXT NOT NULL,
        delivery_state TEXT NOT NULL DEFAULT 'pending')""")


def bind_result(connection: Connection, notification_id: str, result: TaskResult) -> None:
    ensure_bindings(connection)
    if result.identity is None:
        return
    _ = connection.execute(
        """INSERT INTO task_result_deliveries(notification_id,identity_json)
        VALUES(?,?) ON CONFLICT(notification_id) DO NOTHING""",
        (notification_id, result.identity.model_dump_json()),
    )


def matches_result(connection: Connection, notification_id: str, result: TaskResult) -> bool:
    ensure_bindings(connection)
    row = _ROW.validate_python(
        connection.execute(
            "SELECT identity_json FROM task_result_deliveries WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
    )
    if row is None:
        return True
    expected = DeliveryIdentity.model_validate_json(row[0])
    return (
        result.identity == expected
        and contract_sha256({"answer": result.text}) == expected.answer_sha256
    )


def record_delivery(connection: Connection, notification_id: str, state: str) -> None:
    ensure_bindings(connection)
    _ = connection.execute(
        "UPDATE task_result_deliveries SET delivery_state=? WHERE notification_id=?",
        (state, notification_id),
    )


def delivery_view(connection: Connection, tenant_id: str, run_id: str) -> list[JsonObject]:
    ensure_bindings(connection)
    rows = _ROWS.validate_python(
        connection.execute(
            """SELECT identity_json,delivery_state FROM task_result_deliveries
        WHERE json_extract(identity_json,'$.tenant_id')=?
        AND json_extract(identity_json,'$.run_id')=?""",
            (tenant_id, run_id),
        ).fetchall()
    )
    return [
        {
            "identity": DeliveryIdentity.model_validate_json(raw).model_dump(mode="json"),
            "state": state,
        }
        for raw, state in rows
    ]


def read_deliveries(database: Path, tenant_id: str, run_id: str) -> list[JsonObject]:
    with closing(sqlite3.connect(database)) as connection, connection:
        return delivery_view(connection, tenant_id, run_id)


def sync_message_deliveries(connection: Connection) -> None:
    ensure_bindings(connection)
    _ = connection.execute("""UPDATE task_result_deliveries SET delivery_state=(
        SELECT notification_state FROM slack_message_jobs
        WHERE notification_id='message:' || message_id)
        WHERE notification_id IN (SELECT 'message:' || message_id FROM slack_message_jobs)""")


def sync_command_deliveries(connection: Connection) -> None:
    ensure_bindings(connection)
    _ = connection.execute("""UPDATE task_result_deliveries SET delivery_state=(
        SELECT notification_state FROM slack_command_jobs
        WHERE notification_id='command:' || job_id)
        WHERE notification_id IN (SELECT 'command:' || job_id FROM slack_command_jobs)""")

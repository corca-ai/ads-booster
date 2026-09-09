from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

if TYPE_CHECKING:
    import sqlite3

_EVENT_IDS: Final = TypeAdapter(tuple[tuple[str], ...])
READABLE_CONVERSATION_EVENT: Final = """
NOT EXISTS (
    SELECT 1 FROM sources AS event_source
    WHERE event_source.workspace_id=event.workspace_id
    AND event_source.scope_key=event.scope_key
    AND event_source.source_kind='message'
    AND event_source.source_identity='message:' || event.conversation_id || ':' || event.message_id
    AND event_source.visibility='blocked'
)
"""


def source_conversation_event_ids(
    connection: sqlite3.Connection,
    workspace_id: str,
    source_id: str,
) -> tuple[str, ...]:
    """Include every canonical revision owned by this message source in deletion traversal."""
    rows = _EVENT_IDS.validate_python(
        connection.execute(
            """
        SELECT DISTINCT event.message_id FROM conversation_events AS event
        JOIN sources AS source ON source.workspace_id=event.workspace_id
        AND source.scope_key=event.scope_key AND source.source_kind='message'
        AND source.source_identity=
                'message:' || event.conversation_id || ':' || event.message_id
        WHERE source.workspace_id=? AND source.source_id=?
        """,
            (workspace_id, source_id),
        ).fetchall()
    )
    return tuple(row[0] for row in rows)


def scrub_source_conversation_events(
    connection: sqlite3.Connection,
    workspace_id: str,
    source_id: str,
) -> None:
    """Erase canonical message payloads with their source files, preserving event identities."""
    _ = connection.execute(
        """
        UPDATE conversation_events AS event SET event_json='{"redacted":true}'
        WHERE EXISTS (
            SELECT 1 FROM sources AS source
            WHERE source.workspace_id=event.workspace_id AND source.scope_key=event.scope_key
            AND source.source_kind='message' AND source.workspace_id=? AND source.source_id=?
            AND source.source_identity=
                'message:' || event.conversation_id || ':' || event.message_id
        )
        """,
        (workspace_id, source_id),
    )

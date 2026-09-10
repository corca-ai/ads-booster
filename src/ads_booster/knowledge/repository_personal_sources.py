from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic import TypeAdapter

if TYPE_CHECKING:
    import sqlite3

_OPTIONAL_ROW: Final[TypeAdapter[tuple[int] | None]] = TypeAdapter(tuple[int] | None)
_PERSONAL_SOURCE: Final = """
EXISTS (
    SELECT 1 FROM conversation_events AS event
    JOIN derived_memory_refs AS reference ON reference.workspace_id=event.workspace_id
        AND reference.upstream_kind='source' AND reference.upstream_id=event.message_id
    JOIN memory_documents AS document ON document.workspace_id=reference.workspace_id
        AND document.document_id=reference.document_id AND document.kind='user'
        AND (? IS NULL OR document.document_id=?)
    WHERE event.workspace_id=source.workspace_id AND event.scope_key=source.scope_key
        AND source.source_kind='message'
        AND source.source_identity='message:' || event.conversation_id || ':' || event.message_id
)
"""


def personal_source_is_restricted(
    connection: sqlite3.Connection, workspace_id: str, source_id: str
) -> bool:
    row = _OPTIONAL_ROW.validate_python(
        connection.execute(
            f"""SELECT 1 FROM sources AS source
            WHERE source.workspace_id=? AND source.source_id=? AND {_PERSONAL_SOURCE}""",  # noqa: S608 - static SQL predicate; runtime values are bound parameters
            (workspace_id, source_id, None, None),
        ).fetchone()
    )
    return row is not None


def hide_personal_sources(
    connection: sqlite3.Connection, workspace_id: str, document_id: str
) -> None:
    _ = connection.execute(
        f"""UPDATE sources AS source SET visibility='hidden'
        WHERE source.workspace_id=? AND source.visibility!='blocked'
            AND {_PERSONAL_SOURCE}""",  # noqa: S608 - static SQL predicate; runtime values are bound parameters
        (workspace_id, document_id, document_id),
    )

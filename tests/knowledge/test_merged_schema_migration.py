from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.knowledge.migrations import (
    KnowledgeSchemaError,
    connect_database,
    initialize_database,
)
from ads_booster.knowledge.schema_authority import AUTHORITY_SCHEMA
from ads_booster.knowledge.schema_channel import CHANNEL_SCHEMA
from ads_booster.knowledge.schema_content import CONTENT_SCHEMA
from ads_booster.knowledge.schema_deletion import DELETION_SCHEMA
from ads_booster.knowledge.schema_learning import LEARNING_SCHEMA
from ads_booster.knowledge.schema_personal import PERSONAL_SCHEMA
from ads_booster.knowledge.schema_skills import SKILL_SCHEMA
from ads_booster.knowledge.schema_work import WORK_SCHEMA

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("version", "extension"),
    [
        (3, SKILL_SCHEMA),
        (4, SKILL_SCHEMA + LEARNING_SCHEMA),
        (3, CHANNEL_SCHEMA),
        (4, CHANNEL_SCHEMA + PERSONAL_SCHEMA),
    ],
    ids=["published-skills", "published-learning", "candidate-channel", "candidate-user"],
)
def test_divergent_historical_schemas_converge_without_rewriting_evidence(
    tmp_path: Path, version: int, extension: str
) -> None:
    database = tmp_path / "index.sqlite"
    database.touch(mode=0o600)
    schema = AUTHORITY_SCHEMA + CONTENT_SCHEMA + WORK_SCHEMA + DELETION_SCHEMA + extension
    checksum = sha256(schema.encode()).hexdigest()
    with connect_database(database) as connection:
        _ = connection.executescript(schema)
        _ = connection.execute(
            """CREATE TABLE knowledge_schema(version INTEGER PRIMARY KEY,
            checksum TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL)"""
        )
        _ = connection.execute(
            "INSERT INTO knowledge_schema VALUES (?,?,'historical')", (version, checksum)
        )
        _ = connection.execute("INSERT INTO workspaces VALUES ('team',1,'UTC','active')")
        _ = connection.execute(
            """INSERT INTO access_scopes(
            scope_key,kind,workspace_id,member_id,session_id,scope_json)
            VALUES ('scope','workspace','team',NULL,NULL,'{"historical":true}')"""
        )
        _ = connection.execute(
            """INSERT INTO conversation_events VALUES
            ('team','thread','message',1,'created',1,'scope','author',
            '{"immutable":"original evidence"}','2026-09-09')"""
        )
    original_file = tmp_path / "immutable.md"
    _ = original_file.write_bytes(b"original evidence\n")

    _ = initialize_database(tmp_path)
    _ = initialize_database(tmp_path)

    with connect_database(database) as connection:
        history = TypeAdapter(list[tuple[int, str, str]]).validate_python(
            connection.execute(
                "SELECT version,checksum,applied_at FROM knowledge_schema ORDER BY version"
            ).fetchall()
        )
        assert history[0] == (version, checksum, "historical")
        assert history[-1][0] == 6
        assert TypeAdapter(tuple[str]).validate_python(
            connection.execute("SELECT event_json FROM conversation_events").fetchone()
        ) == ('{"immutable":"original evidence"}',)
        tables = TypeAdapter(list[tuple[str]]).validate_python(
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        )
        assert {"skills", "learning_admissions", "channel_grant_admissions"} <= {
            row[0] for row in tables
        }
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert original_file.read_bytes() == b"original evidence\n"


def test_unknown_v4_checksum_is_rejected_without_guessing_schema(tmp_path: Path) -> None:
    database = tmp_path / "index.sqlite"
    database.touch(mode=0o600)
    with connect_database(database) as connection:
        _ = connection.execute(
            """CREATE TABLE knowledge_schema(version INTEGER PRIMARY KEY,
            checksum TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL)"""
        )
        _ = connection.execute("INSERT INTO knowledge_schema VALUES (4,'unknown','historical')")

    with pytest.raises(KnowledgeSchemaError, match="knowledge_schema_unsupported"):
        _ = initialize_database(tmp_path)

    with connect_database(database) as connection:
        assert TypeAdapter(tuple[int]).validate_python(
            connection.execute("SELECT COUNT(*) FROM knowledge_schema").fetchone()
        ) == (1,)

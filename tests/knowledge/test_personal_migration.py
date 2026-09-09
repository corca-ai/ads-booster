from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.knowledge.contract_types import GrantCapability, ScopeKind
from ads_booster.knowledge.migrations import (
    KnowledgeSchemaError,
    connect_database,
    initialize_database,
)
from ads_booster.knowledge.repository_identity import register_actor, scope_key
from ads_booster.knowledge.repository_types import MembershipRole
from ads_booster.knowledge.schema_authority import AUTHORITY_SCHEMA
from ads_booster.knowledge.schema_channel import CHANNEL_SCHEMA
from ads_booster.knowledge.schema_content import CONTENT_SCHEMA
from ads_booster.knowledge.schema_deletion import DELETION_SCHEMA
from ads_booster.knowledge.schema_work import WORK_SCHEMA
from ads_booster.knowledge.scope_contracts import (
    AccessScope,
    ActorContext,
    ScopeGrant,
    channel_member_scope,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 9, tzinfo=UTC)
_V3_HASH = "fbe7c3c85451112345f4adb58ad0d803a6df5509b5b90a52aa2d003b19357894"


def _actor(member: str = "U1", channel: str = "CA", *, personal: bool = False) -> ActorContext:
    channel_scope = AccessScope(kind=ScopeKind.CHANNEL, workspace_id="T1", channel_id=channel)
    target = (
        AccessScope(
            kind=ScopeKind.CHANNEL_MEMBER, workspace_id="T1", channel_id=channel, member_id=member
        )
        if personal
        else channel_scope
    )
    return ActorContext(
        actor_id=member,
        member_id=member,
        workspace_id="T1",
        session_id=f"session.{member}.{channel}",
        conversation_scope=channel_scope,
        authenticated_at=NOW,
        policy_epoch=1,
        grants=(
            ScopeGrant(
                grant_id=f"grant.{member}.{channel}.{personal}",
                capability=GrantCapability.READ,
                workspace_id="T1",
                scope=target,
                policy_epoch=1,
                effective_at=NOW,
            ),
        ),
    )


def _v3_database(root: Path) -> Path:
    database = root / "index.sqlite"
    database.touch(mode=0o600)
    schema = AUTHORITY_SCHEMA + CONTENT_SCHEMA + WORK_SCHEMA + DELETION_SCHEMA + CHANNEL_SCHEMA
    assert sha256(schema.encode()).hexdigest() == _V3_HASH
    with connect_database(database) as connection:
        _ = connection.executescript(schema)
        _ = connection.execute(
            """CREATE TABLE knowledge_schema(version INTEGER PRIMARY KEY,
            checksum TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL)"""
        )
        _ = connection.execute(
            "INSERT INTO knowledge_schema VALUES (3,?,'2026-09-09')", (_V3_HASH,)
        )
        actor = _actor()
        register_actor(connection, actor, MembershipRole.EDITOR)
        _ = connection.execute(
            """INSERT INTO memory_documents VALUES
            ('T1','legacy','core',NULL,NULL,'UTC',?,'{"provenance":"unchanged document"}')""",
            (scope_key(actor.conversation_scope),),
        )
        _ = connection.execute(
            """INSERT INTO memory_revisions VALUES
            ('T1','legacy','rev1',NULL,?,'teams/T1/revisions/legacy/rev1.md',
             '{"provenance":"unchanged revision"}','operation', '2026-09-09')""",
            (sha256(b"existing memory\n").hexdigest(),),
        )
        _ = connection.execute(
            """INSERT INTO memory_view_outbox(item_id,workspace_id,document_id,revision_id,
                view_kind,unique_key,state)
            VALUES ('legacy-view','T1','legacy','rev1','core','legacy-view','pending')"""
        )
    body = root / "teams/T1/revisions/legacy/rev1.md"
    body.parent.mkdir(parents=True)
    _ = body.write_bytes(b"existing memory\n")
    return database


def test_v3_migration_preserves_documents_revisions_views_and_files(tmp_path: Path) -> None:
    database = _v3_database(tmp_path)
    with connect_database(database) as connection:
        previous = tuple(
            connection.execute(query).fetchall()
            for query in (
                "SELECT * FROM memory_documents",
                "SELECT * FROM memory_revisions",
                "SELECT * FROM memory_view_outbox",
                "SELECT * FROM access_scopes",
            )
        )
    _ = initialize_database(tmp_path)
    _ = initialize_database(tmp_path)
    with connect_database(database) as connection:
        current = tuple(
            connection.execute(query).fetchall()
            for query in (
                "SELECT * FROM memory_documents",
                "SELECT * FROM memory_revisions",
                "SELECT * FROM memory_view_outbox",
                "SELECT * FROM access_scopes",
            )
        )
        assert current == previous
        assert TypeAdapter(tuple[int]).validate_python(
            connection.execute("SELECT MAX(version) FROM knowledge_schema").fetchone()
        ) == (6,)
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert (tmp_path / "teams/T1/revisions/legacy/rev1.md").read_bytes() == b"existing memory\n"


def test_user_document_identity_is_per_channel_member(tmp_path: Path) -> None:
    database = initialize_database(tmp_path)
    with connect_database(database) as connection:
        for member, channel in (("U1", "CA"), ("U2", "CA"), ("U1", "CB")):
            actor = _actor(member, channel, personal=True)
            register_actor(connection, actor, MembershipRole.EDITOR)
            target = channel_member_scope(actor)
            assert target is not None
            _ = connection.execute(
                "INSERT INTO memory_documents VALUES (?,?,'user',NULL,NULL,'UTC',?,'{}')",
                ("T1", f"user.{member}.{channel}", scope_key(target)),
            )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _ = connection.execute(
                "INSERT INTO memory_documents VALUES (?,?,'user',NULL,NULL,'UTC',?,'{}')",
                ("T1", "duplicate", scope_key(actor.grants[0].scope)),
            )
        assert TypeAdapter(tuple[int]).validate_python(
            connection.execute("SELECT COUNT(*) FROM memory_documents").fetchone()
        ) == (3,)


def test_v4_migration_failure_preserves_v3_schema_and_data(tmp_path: Path) -> None:
    database = _v3_database(tmp_path)
    with connect_database(database) as connection:
        _ = connection.execute("PRAGMA foreign_keys=OFF")
        _ = connection.execute("UPDATE memory_documents SET scope_key='missing'")
    with pytest.raises(KnowledgeSchemaError, match="knowledge_schema_foreign_key_invalid"):
        _ = initialize_database(tmp_path)
    with connect_database(database) as connection:
        assert TypeAdapter(tuple[int]).validate_python(
            connection.execute("SELECT MAX(version) FROM knowledge_schema").fetchone()
        ) == (3,)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            _ = connection.execute(
                "UPDATE memory_documents SET kind='user' WHERE document_id='legacy'"
            )

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.change_validation import ChangeValidationError, require_scope_not_wider
from ads_booster.knowledge.contract_types import GrantCapability, ScopeKind
from ads_booster.knowledge.errors import AccessDeniedError, ScopeIntersectionError
from ads_booster.knowledge.grant_policy import (
    authorize_read,
    authorize_write,
    intersect_lineage_scopes,
)
from ads_booster.knowledge.migrations import (
    KnowledgeSchemaError,
    connect_database,
    initialize_database,
)
from ads_booster.knowledge.repository_identity import register_actor, scope_key
from ads_booster.knowledge.repository_types import MembershipRole
from ads_booster.knowledge.schema_authority import AUTHORITY_SCHEMA
from ads_booster.knowledge.schema_content import CONTENT_SCHEMA
from ads_booster.knowledge.schema_deletion import DELETION_SCHEMA
from ads_booster.knowledge.schema_work import WORK_SCHEMA
from ads_booster.knowledge.scope_contracts import AccessScope, ActorContext, ScopeGrant

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def _channel(channel_id: str) -> AccessScope:
    return AccessScope.model_validate(
        {"kind": "channel", "workspace_id": "T1", "channel_id": channel_id}
    )


def _actor(scope: AccessScope, grant_scope: AccessScope | None = None) -> ActorContext:
    target = grant_scope or scope
    return ActorContext(
        actor_id="U1",
        workspace_id="T1",
        member_id="U1",
        session_id="session1",
        conversation_scope=scope,
        policy_epoch=1,
        authenticated_at=NOW,
        grants=tuple(
            ScopeGrant(
                grant_id=f"grant.{capability.value}.{scope_key(target)}",
                capability=capability,
                workspace_id="T1",
                scope=target,
                policy_epoch=1,
                effective_at=NOW,
            )
            for capability in (GrantCapability.READ, GrantCapability.WRITE)
        ),
    )


@pytest.mark.parametrize(
    "legacy",
    [
        '{"kind":"workspace","workspace_id":"T1","member_id":null,"session_id":null}',
        '{"kind":"member","workspace_id":"T1","member_id":"U1","session_id":"session1"}',
    ],
)
def test_legacy_scope_serialization_is_byte_stable(legacy: str) -> None:
    # Given an already persisted scope whose canonical digest is referenced by evidence.
    scope = AccessScope.model_validate_json(legacy)
    # When serialized by the upgraded contract.
    serialized = scope.model_dump_json()
    # Then existing bytes and the canonical digest remain unchanged.
    assert serialized == legacy
    canonical = json.dumps(json.loads(legacy), sort_keys=True, separators=(",", ":"))
    assert contract_sha256(scope) == sha256(canonical.encode()).hexdigest()


def test_channel_grant_denies_other_channel_and_legacy_workspace() -> None:
    # Given an actor authorized for just channel A.
    actor = _actor(_channel("CA"))
    # When trying to read channel B or unknown-channel legacy data.
    for target in (_channel("CB"), AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="T1")):
        # Then exact-scope authorization denies both.
        with pytest.raises(AccessDeniedError):
            _ = authorize_read(actor=actor, target_scope=target, at=NOW)


def test_private_actor_cannot_write_channel_even_with_grant() -> None:
    # Given a private conversation and an explicit shared write grant.
    channel = _channel("CA")
    actor = _actor(
        AccessScope(
            kind=ScopeKind.MEMBER, workspace_id="T1", member_id="U1", session_id="session1"
        ),
        channel,
    )
    # When trying to mutate channel memory, then the private/shared boundary denies it.
    with pytest.raises(AccessDeniedError, match="private_shared_write_forbidden"):
        _ = authorize_write(actor=actor, target_scope=channel, at=NOW)


def test_channel_lineage_cannot_expand_or_cross_channels() -> None:
    # Given channel A evidence and unrelated destinations.
    source = _channel("CA")
    targets = (_channel("CB"), AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="T1"))
    # When deriving knowledge into either destination, then widening is rejected.
    for target in targets:
        with pytest.raises(ChangeValidationError, match="scope_expansion_forbidden"):
            require_scope_not_wider(source=source, target=target, target_id="derived")
    with pytest.raises(ScopeIntersectionError):
        _ = intersect_lineage_scopes((source, _channel("CB")))
    assert intersect_lineage_scopes((source, source)) == source


@pytest.mark.parametrize("version", [1, 2])
def test_upgrade_preserves_existing_provenance_and_accepts_channel(
    tmp_path: Path, version: int
) -> None:
    # Given an actual previous-schema database with a persisted conversation event.
    database = tmp_path / "index.sqlite"
    database.touch(mode=0o600)
    previous_schema = AUTHORITY_SCHEMA + CONTENT_SCHEMA + WORK_SCHEMA
    if version == 2:
        previous_schema += DELETION_SCHEMA
    historical_checksum = {
        1: "21929f40dd1232c05c13426a811e71be577956f531c42568201e3644f0ff9c18",
        2: "eeecf48cbc20ab5017d9e9893e06e16b3832bd5cb54f4825aff3c2110244d2eb",
    }[version]
    assert sha256(previous_schema.encode()).hexdigest() == historical_checksum
    actor = _actor(AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="T1"))
    event_json = '{"legacy":"immutable provenance"}'
    with connect_database(database) as connection:
        _ = connection.executescript(previous_schema)
        _ = connection.execute(
            """CREATE TABLE knowledge_schema(version INTEGER PRIMARY KEY,
            checksum TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL)"""
        )
        _ = connection.execute(
            "INSERT INTO knowledge_schema VALUES (?,?,'2026-09-08')",
            (version, historical_checksum),
        )
        _ = connection.execute("INSERT INTO workspaces VALUES ('T1',1,'UTC','active')")
        _ = connection.execute(
            "INSERT INTO access_scopes VALUES (?,'workspace','T1',NULL,NULL,?)",
            (scope_key(actor.conversation_scope), actor.conversation_scope.model_dump_json()),
        )
        _ = connection.execute(
            """INSERT INTO conversation_events VALUES
            ('T1','conversation','message',1,'created',1,?,'U1',?,'2026-09-08')""",
            (scope_key(actor.conversation_scope), event_json),
        )
        _ = connection.execute(
            "INSERT INTO brands VALUES ('T1','brand.legacy','Legacy',1,'active',?)",
            ('{"legacy":"brand provenance"}',),
        )
    # When opening twice with the upgraded owner and adding a channel identity.
    _ = initialize_database(tmp_path)
    _ = initialize_database(tmp_path)
    with connect_database(database) as connection:
        register_actor(connection, _actor(_channel("CA")), MembershipRole.EDITOR)
        # Then original bytes/keys remain, and the channel persists under the real tenant.
        row = TypeAdapter(tuple[str, str]).validate_python(
            connection.execute("SELECT scope_key,event_json FROM conversation_events").fetchone()
        )
        assert tuple(row) == (scope_key(actor.conversation_scope), event_json)
        assert TypeAdapter(tuple[str, str]).validate_python(
            connection.execute(
                "SELECT workspace_id,channel_id FROM access_scopes WHERE kind='channel'"
            ).fetchone()
        ) == ("T1", "CA")
        assert TypeAdapter(tuple[str, str]).validate_python(
            connection.execute("SELECT scope_key,brand_json FROM brands").fetchone()
        ) == (scope_key(actor.conversation_scope), '{"legacy":"brand provenance"}')
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize(
    ("kind", "local_date"), [("team", None), ("core", None), ("daily", "2026-09-09")]
)
def test_memory_identity_is_unique_within_each_channel(
    tmp_path: Path, kind: str, local_date: str | None
) -> None:
    database = initialize_database(tmp_path)
    with connect_database(database) as connection:
        for channel in ("CA", "CB"):
            actor = _actor(_channel(channel))
            register_actor(connection, actor, MembershipRole.EDITOR)
            _ = connection.execute(
                """INSERT INTO memory_documents VALUES (?, ?, ?, NULL, ?, 'UTC', ?, '{}')""",
                (
                    "T1",
                    f"document.{channel}",
                    kind,
                    local_date,
                    scope_key(actor.conversation_scope),
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _ = connection.execute(
                """INSERT INTO memory_documents VALUES (?, ?, ?, NULL, ?, 'UTC', ?, '{}')""",
                ("T1", "duplicate", kind, local_date, scope_key(_channel("CA"))),
            )
        assert TypeAdapter(tuple[int]).validate_python(
            connection.execute("SELECT COUNT(*) FROM memory_documents").fetchone()
        ) == (2,)


@pytest.mark.parametrize(
    "scope_payload",
    [
        {"kind": "channel", "workspace_id": "T1"},
        {"kind": "channel", "workspace_id": "T1", "channel_id": "CA", "member_id": "U1"},
        {"kind": "workspace", "workspace_id": "T1", "channel_id": "CA"},
        {
            "kind": "member",
            "workspace_id": "T1",
            "member_id": "U1",
            "session_id": "S1",
            "channel_id": "CA",
        },
    ],
)
def test_channel_scope_rejects_ambiguous_identity(scope_payload: Mapping[str, str]) -> None:
    with pytest.raises(ValidationError):
        _ = AccessScope.model_validate(scope_payload)


def test_failed_migration_rolls_back_ddl_and_keeps_version(tmp_path: Path) -> None:
    database = tmp_path / "index.sqlite"
    database.touch(mode=0o600)
    previous_schema = AUTHORITY_SCHEMA + CONTENT_SCHEMA + WORK_SCHEMA + DELETION_SCHEMA
    with connect_database(database) as connection:
        _ = connection.executescript(previous_schema)
        _ = connection.execute(
            """CREATE TABLE knowledge_schema(version INTEGER PRIMARY KEY,
            checksum TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL)"""
        )
        _ = connection.execute(
            "INSERT INTO knowledge_schema VALUES (2,?,'2026-09-08')",
            (sha256(previous_schema.encode()).hexdigest(),),
        )
        _ = connection.execute("PRAGMA foreign_keys=OFF")
        _ = connection.execute(
            """INSERT INTO conversation_events VALUES
            ('T1','orphan','message',1,'created',1,'missing-scope','U1','{}','2026-09-08')"""
        )
    with pytest.raises(KnowledgeSchemaError, match="knowledge_schema_foreign_key_invalid"):
        _ = initialize_database(tmp_path)
    with connect_database(database) as connection:
        assert TypeAdapter(tuple[int]).validate_python(
            connection.execute("SELECT MAX(version) FROM knowledge_schema").fetchone()
        ) == (2,)
        assert TypeAdapter(tuple[int]).validate_python(
            connection.execute(
                "SELECT COUNT(*) FROM pragma_table_info('access_scopes') WHERE name='channel_id'"
            ).fetchone()
        ) == (0,)
        assert TypeAdapter(tuple[int]).validate_python(
            connection.execute("SELECT COUNT(*) FROM conversation_events").fetchone()
        ) == (1,)


def test_channel_grant_admission_survives_grant_deletion(tmp_path: Path) -> None:
    database = initialize_database(tmp_path)
    actor = _actor(_channel("CA"))
    grant = actor.grants[0]
    with connect_database(database) as connection:
        register_actor(connection, actor, MembershipRole.EDITOR)
        _ = connection.execute(
            """INSERT INTO channel_grant_admissions(
                workspace_id,member_id,scope_key,capability,grant_id,policy_epoch
            ) VALUES (?,?,?,?,?,?)""",
            (
                actor.workspace_id,
                actor.member_id,
                scope_key(grant.scope),
                grant.capability.value,
                grant.grant_id,
                grant.policy_epoch,
            ),
        )
        _ = connection.execute(
            "DELETE FROM scope_grants WHERE workspace_id=? AND grant_id=?",
            (actor.workspace_id, grant.grant_id),
        )
    _ = initialize_database(tmp_path)
    with connect_database(database) as connection:
        assert TypeAdapter(tuple[str, int, str]).validate_python(
            connection.execute(
                "SELECT grant_id,policy_epoch,state FROM channel_grant_admissions"
            ).fetchone()
        ) == (grant.grant_id, grant.policy_epoch, "active")
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()

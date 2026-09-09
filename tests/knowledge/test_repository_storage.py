from __future__ import annotations

import sqlite3
import stat
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.knowledge.repository import SqliteKnowledgeRepository

if TYPE_CHECKING:
    from pathlib import Path


_TABLE_ROWS: TypeAdapter[list[tuple[str]]] = TypeAdapter(list[tuple[str]])
_VERSION_ROW: TypeAdapter[tuple[int, int] | None] = TypeAdapter(tuple[int, int] | None)


def test_fresh_catalog_has_normalized_schema_and_private_database(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")

    assert stat.S_IMODE(repository.database_path.stat().st_mode) == 0o600
    with repository.connection() as connection:
        table_rows = _TABLE_ROWS.validate_python(
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        )
        tables = {row[0] for row in table_rows}
        version = _VERSION_ROW.validate_python(
            connection.execute(
                """SELECT version, length(checksum) FROM knowledge_schema
                ORDER BY version DESC LIMIT 1"""
            ).fetchone()
        )

    assert {
        "access_scopes",
        "claims",
        "context_dependencies",
        "deletion_dependency_blocks",
        "deletion_manifest_entries",
        "deletion_requests",
        "evidence_edges",
        "history_redactions",
        "jobs",
        "knowledge_heads",
        "knowledge_revisions",
        "learning_admissions",
        "learning_batch_partitions",
        "learning_consumed_targets",
        "learning_counters",
        "learning_rounds",
        "memory_documents",
        "memory_revisions",
        "operations",
        "replica_purge_receipts",
        "segments",
        "source_revisions",
        "sources",
        "skill_heads",
        "skill_revisions",
        "skills",
        "wiki_pages",
    } <= tables
    assert version is not None
    assert version == (6, 64)


def test_scope_and_memory_identity_constraints_reject_cross_tenant_rows(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")

    with repository.connection() as connection:
        for workspace_id in ("workspace.a", "workspace.b"):
            _ = connection.execute(
                "INSERT INTO workspaces VALUES (?,1,'UTC','active')",
                (workspace_id,),
            )
        _ = connection.execute(
            """
            INSERT INTO access_scopes(
                scope_key,kind,workspace_id,member_id,session_id,scope_json
            ) VALUES ('workspace:workspace.a','workspace','workspace.a',NULL,NULL,'{}'),
                ('workspace:workspace.b','workspace','workspace.b',NULL,NULL,'{}')
            """
        )
        _ = connection.execute(
            """
            INSERT INTO brands(workspace_id,brand_id,name,revision,state,brand_json,scope_key)
            VALUES ('workspace.b','brand.b','Brand B',1,'active','{}','workspace:workspace.b')
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO access_scopes(
                    scope_key,kind,workspace_id,member_id,session_id,scope_json
                ) VALUES ('invalid','workspace','workspace.a','member.workspace.a',NULL,'{}')
                """
            )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO memory_documents(
                    workspace_id,document_id,kind,brand_id,local_date,timezone,
                    scope_key,document_json
                ) VALUES (
                    'workspace.a','memory.cross','soul','brand.b',NULL,'UTC',
                    'workspace:workspace.a','{}'
                )
                """
            )
        _ = connection.execute(
            """
            INSERT INTO memory_documents(
                workspace_id,document_id,kind,brand_id,local_date,timezone,
                scope_key,document_json
            ) VALUES (
                'workspace.a','memory.team.one','team',NULL,NULL,'UTC',
                'workspace:workspace.a','{}'
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                """
                INSERT INTO memory_documents(
                    workspace_id,document_id,kind,brand_id,local_date,timezone,
                    scope_key,document_json
                ) VALUES (
                    'workspace.a','memory.team.two','team',NULL,NULL,'UTC',
                    'workspace:workspace.a','{}'
                )
                """
            )

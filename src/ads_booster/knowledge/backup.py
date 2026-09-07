from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from pydantic import Field

from ads_booster.contracts.models import ContractModel

if TYPE_CHECKING:
    from ads_booster.knowledge.maintenance import KnowledgeActivity
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository


class BackupFile(ContractModel):
    relative_path: str
    sha256: str
    byte_length: int


class KnowledgeBackupManifest(ContractModel):
    schema_version: Literal["trace.knowledge-backup.v1"] = Field(alias="schema")
    backup_id: str
    workspace_id: str
    erase_sequence: int
    erase_head_sha256: str
    created_at: datetime
    database_sha256: str
    files: tuple[BackupFile, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeBackup:
    backup_id: str
    path: Path
    manifest: KnowledgeBackupManifest


def create_backup(
    repository: SqliteKnowledgeRepository,
    destination: Path,
    *,
    workspace_id: str,
    erase_sequence: int,
    erase_head_sha256: str,
    activity: KnowledgeActivity | None = None,
) -> KnowledgeBackup:
    if not destination.is_absolute():
        raise ValueError("knowledge_backup_destination_must_be_absolute")
    if activity is not None:
        admitted = activity.begin_exclusive("backup")
        if not admitted:
            activity.resume()
            raise RuntimeError("knowledge_backup_active_work")
    try:
        backup_id = uuid4().hex
        target = destination / backup_id
        target.mkdir(mode=0o700, parents=True, exist_ok=False)
        database = target / "catalog.sqlite3"
        with repository.connection() as source, sqlite3.connect(database) as sink:
            source.backup(sink)
        os.chmod(database, 0o600)
        committed = _committed_files(database, repository.root, workspace_id)
        copied: list[BackupFile] = []
        for item in committed:
            source = _inside(repository.root, item.relative_path)
            if not source.is_file():
                raise RuntimeError(f"knowledge_backup_file_missing:{item.relative_path}")
            body = source.read_bytes()
            if len(body) != item.byte_length or sha256(body).hexdigest() != item.sha256:
                raise RuntimeError(f"knowledge_backup_file_integrity:{item.relative_path}")
            output = _inside(target / "files", item.relative_path)
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            output.write_bytes(body)
            output.chmod(0o600)
            copied.append(item)
        manifest = KnowledgeBackupManifest(
            schema="trace.knowledge-backup.v1",
            backup_id=backup_id,
            workspace_id=workspace_id,
            erase_sequence=erase_sequence,
            erase_head_sha256=erase_head_sha256,
            created_at=datetime.now(UTC),
            database_sha256=sha256(database.read_bytes()).hexdigest(),
            files=tuple(copied),
        )
        manifest_path = target / "manifest.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        manifest_path.chmod(0o600)
        return KnowledgeBackup(backup_id, target, manifest)
    finally:
        if activity is not None:
            activity.finish_exclusive("backup")


def _committed_files(
    database: Path,
    source_root: Path,
    workspace_id: str,
) -> tuple[BackupFile, ...]:
    with sqlite3.connect(database) as connection:
        rows = cast(
            "list[tuple[object, ...]]",
            connection.execute(
                """
                SELECT relative_path,sha256,byte_length FROM source_files WHERE workspace_id=?
                UNION ALL
                SELECT relative_path,body_sha256,length(CAST('' AS BLOB))
                FROM knowledge_revisions WHERE workspace_id=?
                UNION ALL
                SELECT relative_path,body_sha256,length(CAST('' AS BLOB))
                FROM memory_revisions WHERE workspace_id=?
                ORDER BY relative_path
                """,
                (workspace_id, workspace_id, workspace_id),
            ).fetchall(),
        )
    result: list[BackupFile] = []
    for relative, digest, length in rows:
        relative_path = str(relative)
        file_path = _inside(source_root, relative_path)
        byte_length = int(str(length))
        if byte_length == 0 and file_path.exists():
            byte_length = file_path.stat().st_size
        result.append(BackupFile(relative_path=relative_path, sha256=str(digest), byte_length=byte_length))
    return tuple(result)


def _inside(root: Path, relative_path: str) -> Path:
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError("knowledge_backup_path_invalid")
    candidate = root.joinpath(*pure.parts)
    if not candidate.resolve(strict=False).is_relative_to(root.resolve()):
        raise ValueError("knowledge_backup_path_invalid")
    return candidate


__all__ = [
    "BackupFile",
    "KnowledgeBackup",
    "KnowledgeBackupManifest",
    "create_backup",
]

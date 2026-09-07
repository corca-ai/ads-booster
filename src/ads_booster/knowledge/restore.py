from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from ads_booster.knowledge.backup import KnowledgeBackupManifest, _inside
from ads_booster.knowledge.erase_ledger import EraseLedgerExport, RestoreAuthorityReceipt
from ads_booster.knowledge.repository import SqliteKnowledgeRepository


class RestoreEraseAuthority(Protocol):
    def export(self, workspace_id: str) -> EraseLedgerExport: ...

    def apply_to_restored_target(
        self,
        repository: SqliteKnowledgeRepository,
        exported: EraseLedgerExport,
    ) -> RestoreAuthorityReceipt: ...

    def verify_restore_authority(
        self,
        workspace_id: str,
        observed_sequence: int,
        observed_head_sha256: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class RestoreReceipt:
    target: Path
    backup_id: str
    workspace_id: str
    erase_sequence: int


def restore_backup(
    backup: Path,
    target: Path,
    *,
    erase_authority: RestoreEraseAuthority,
) -> RestoreReceipt:
    if not backup.is_absolute() or not target.is_absolute():
        raise ValueError("knowledge_restore_requires_absolute_paths")
    if target.exists():
        raise ValueError("knowledge_restore_target_must_be_new")
    manifest = KnowledgeBackupManifest.model_validate_json((backup / "manifest.json").read_bytes())
    exported = erase_authority.export(manifest.workspace_id)
    current_sequence = exported.sequence
    current_head = exported.head_sha256
    erase_authority.verify_restore_authority(
        manifest.workspace_id,
        current_sequence,
        current_head,
    )
    if current_sequence < manifest.erase_sequence:
        raise ValueError("knowledge_restore_erase_authority_stale")
    if current_sequence == manifest.erase_sequence and current_head != manifest.erase_head_sha256:
        raise ValueError("knowledge_restore_erase_authority_fork")
    database = backup / "catalog.sqlite3"
    if sha256(database.read_bytes()).hexdigest() != manifest.database_sha256:
        raise ValueError("knowledge_restore_database_integrity")
    staging = target.with_name(f".{target.name}.restoring")
    if staging.exists():
        raise ValueError("knowledge_restore_staging_exists")
    staging.mkdir(mode=0o700, parents=True)
    try:
        shutil.copy2(database, staging / "index.sqlite")
        os.chmod(staging / "index.sqlite", 0o600)
        for item in manifest.files:
            source = _inside(backup / "files", item.relative_path)
            body = source.read_bytes()
            if len(body) != item.byte_length or sha256(body).hexdigest() != item.sha256:
                raise ValueError(f"knowledge_restore_file_integrity:{item.relative_path}")
            output = _inside(staging, item.relative_path)
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            output.write_bytes(body)
            output.chmod(0o600)
        repository = SqliteKnowledgeRepository(staging)
        _ = erase_authority.apply_to_restored_target(repository, exported)
        _rebuild_search(repository)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return RestoreReceipt(target, manifest.backup_id, manifest.workspace_id, current_sequence)


def _rebuild_search(repository: SqliteKnowledgeRepository) -> None:
    with repository.connection() as connection:
        _ = connection.execute("BEGIN IMMEDIATE")
        _ = connection.execute("DELETE FROM chunks_fts")
        _ = connection.execute(
            "INSERT INTO chunks_fts(chunk_id,content,index_content) SELECT chunk_id,content,index_content FROM chunks"
        )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise ValueError("knowledge_restore_database_invalid")


__all__ = ["RestoreEraseAuthority", "RestoreReceipt", "restore_backup"]

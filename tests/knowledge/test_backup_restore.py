from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.backup import create_backup
from ads_booster.knowledge.change_publication import ChangeGroup, ChangePublisher, MemoryPublication
from ads_booster.knowledge.contracts import (
    GrantCapability,
    MemoryKind,
    MemoryOperation,
    MemoryOperationKind,
)
from ads_booster.knowledge.deletion import (
    DeletionService,
    PurgeRequest,
    ReplicaPurgeReceipt,
    ReplicaPurgeRequest,
)
from ads_booster.knowledge.erase_ledger import EraseLedger, EraseTarget
from ads_booster.knowledge.memory import MemorySnapshot
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.restore import restore_backup
from tests.knowledge.change_test_fixtures import (
    NOW,
    actor,
    memory_document,
    memory_entry,
    memory_snapshot_parts,
    register_evidence_source,
)

if TYPE_CHECKING:
    from pathlib import Path


class NoReplicas:
    def purge(self, request: ReplicaPurgeRequest) -> ReplicaPurgeReceipt:
        raise AssertionError(request.replica_id)


def test_backup_manifest_round_trip_and_file_integrity(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    _ = register_evidence_source(repository, body="가격표 유지 retain this source".encode())
    ledger = EraseLedger(tmp_path / "control")
    _ = ledger.initialize()
    backup = create_backup(
        repository,
        tmp_path / "backups",
        workspace_id=editor.workspace_id,
        erase_sequence=0,
        erase_head_sha256="0" * 64,
    )
    restored = restore_backup(backup.path, tmp_path / "restored", erase_authority=ledger)
    restored_repository = SqliteKnowledgeRepository(restored.target)
    source = restored_repository.read_source(editor, "source.fact")
    assert source is not None
    item = backup.manifest.files[0]
    _ = (backup.path / "files" / item.relative_path).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="knowledge_restore_file_integrity"):
        _ = restore_backup(backup.path, tmp_path / "corrupt", erase_authority=ledger)
    assert not (tmp_path / "corrupt").exists()


def test_current_erase_ledger_cleans_old_backup_and_preserves_mixed_memory(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor(
        capabilities=(GrantCapability.READ, GrantCapability.WRITE, GrantCapability.PURGE)
    )
    repository.register_actor(editor, MembershipRole.ADMIN)
    _, event_ref = register_evidence_source(
        repository, body=b"Adopt concise reporting", conversation=True
    )
    assert event_ref is not None
    document = memory_document(kind=MemoryKind.TEAM, document_id="memory.team")
    removed = memory_entry(document=document, entry_id="entry.removed").model_copy(
        update={"source_refs": (event_ref,)}
    )
    survivor = removed.model_copy(
        update={"entry_id": "entry.survivor", "text": "Surviving instruction"}
    )
    document, revision, entries, body = memory_snapshot_parts(
        document=document, entries=(removed, survivor)
    )
    operation_id = "operation.memory.create"
    _ = ChangePublisher(repository).publish(
        actor=editor,
        group=ChangeGroup(
            operation_id=operation_id,
            memory_operations=(
                MemoryOperation(
                    operation_id=operation_id,
                    document_id=document.document_id,
                    expected_revision_id="none",
                    kind=MemoryOperationKind.UPDATE,
                    entry_id="entry.memory.team",
                    reason="Publish mixed memory for restore regression.",
                    evidence_refs=("event.seed",),
                ),
            ),
        ),
        pages=None,
        memories=(MemoryPublication(MemorySnapshot(document, revision, entries, body)),),
        at=NOW,
    )
    ledger = EraseLedger(tmp_path / "control")
    _ = ledger.initialize()
    backup = create_backup(
        repository,
        tmp_path / "backups",
        workspace_id=editor.workspace_id,
        erase_sequence=0,
        erase_head_sha256="0" * 64,
    )
    service = DeletionService(repository, ledger, NoReplicas())
    _ = service.request_purge(
        PurgeRequest(
            request_id="purge.0",
            actor=editor,
            target=EraseTarget(kind="memory_entry", entity_id=removed.entry_id),
            reason_code="test",
            explicit_admin_request=True,
            occurred_at=NOW,
        )
    )
    restored = restore_backup(backup.path, tmp_path / "restored", erase_authority=ledger)
    restored_repository = SqliteKnowledgeRepository(restored.target)
    memory = restored_repository.read_memory(editor, document.document_id)
    assert memory is not None
    assert memory.entries == (survivor,)
    assert removed.entry_id not in memory.revision.entry_ids
    assert b"Surviving instruction" in memory.body
    assert (
        restored_repository.read_memory(editor, document.document_id, revision.revision_id) is None
    )
    assert restored.erase_sequence == 1
    _ = service.request_purge(
        PurgeRequest(
            request_id="purge.source",
            actor=editor,
            target=EraseTarget(kind="source", entity_id="source.decision"),
            reason_code="test",
            explicit_admin_request=True,
            occurred_at=NOW,
        )
    )
    erased = restore_backup(backup.path, tmp_path / "erased", erase_authority=ledger)
    assert SqliteKnowledgeRepository(erased.target).read_source(editor, "source.decision") is None

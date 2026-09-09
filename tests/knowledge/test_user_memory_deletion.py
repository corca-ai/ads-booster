from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.backup import create_backup
from ads_booster.knowledge.contract_types import GrantCapability
from ads_booster.knowledge.deletion import DeletionService, PurgeRequest, RetractionRequest
from ads_booster.knowledge.erase_ledger import EraseLedger, EraseTarget
from ads_booster.knowledge.file_paths import MemoryRevisionTarget
from ads_booster.knowledge.memory_consolidation_views import MemoryViewDispatcher, memory_view_path
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.restore import restore_backup
from tests.knowledge.test_backup_restore import NoReplicas
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input
from tests.knowledge.test_user_curation_memory import member_work, remember_user

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.knowledge.repository_types import StoredMemory
    from ads_booster.knowledge.scope_contracts import ActorContext
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


@pytest.mark.parametrize("stage", ["retract", "purge", "direct_purge", "restore"])
def test_user_materialized_view_forgets_deleted_source_and_preserves_other_member(
    curation_input: CurationInput,
    tmp_path: Path,
    stage: str,
) -> None:
    # Given
    repository = curation_input[0]
    first = member_work(curation_input, "alice", "first", "SECRET-ALICE-REMOVED")
    first_id = remember_user(curation_input, first, "SECRET-ALICE-REMOVED")
    second = member_work(curation_input, "bob", "second", "BOB-SURVIVING")
    second_id = remember_user(curation_input, second, "BOB-SURVIVING")
    first_memory = _materialize(repository, first.trusted_context.actor, first_id)
    second_memory = _materialize(repository, second.trusted_context.actor, second_id)
    original_revision_file = repository.files.published(
        MemoryRevisionTarget(
            workspace_id=first_memory.document.workspace_id,
            document_id=first_id,
            revision_id=first_memory.revision.revision_id,
        ),
        first_memory.revision.body_sha256,
    )
    canonical_path = repository.files.root / original_revision_file.relative_path
    first_relative = memory_view_path(repository.files.root, first_memory.document).relative_to(
        repository.files.root,
    )
    second_relative = memory_view_path(repository.files.root, second_memory.document).relative_to(
        repository.files.root,
    )
    assert "SECRET-ALICE-REMOVED" in (repository.files.root / first_relative).read_text()
    editor = first.trusted_context.actor.model_copy(
        update={
            "grants": (
                *first.trusted_context.actor.grants,
                *(
                    grant.model_copy(
                        update={
                            "grant_id": f"purge.{grant.grant_id}",
                            "capability": GrantCapability.PURGE,
                        }
                    )
                    for grant in first.trusted_context.actor.grants
                    if grant.capability is GrantCapability.WRITE
                ),
            )
        }
    )
    repository.register_actor(editor, MembershipRole.ADMIN)
    ledger = EraseLedger(tmp_path / "erase")
    _ = ledger.initialize()
    backup = create_backup(
        repository,
        tmp_path / "backup",
        workspace_id=editor.workspace_id,
        erase_sequence=0,
        erase_head_sha256="0" * 64,
    )
    service = DeletionService(repository, ledger, NoReplicas())
    target = EraseTarget(kind="source", entity_id=first.request.excerpts[0].source_id)
    # When
    if stage != "direct_purge":
        _ = service.retract(
            RetractionRequest(
                operation_id="retract.user",
                actor=editor,
                target=target,
                reason_code="test",
                occurred_at=datetime.now(UTC),
            )
        )
    if stage != "retract":
        _ = service.request_purge(
            PurgeRequest(
                request_id="purge.user",
                actor=editor,
                target=target,
                reason_code="test",
                explicit_admin_request=True,
                occurred_at=datetime.now(UTC),
            )
        )
    if stage == "restore":
        restored = restore_backup(backup.path, tmp_path / "restored", erase_authority=ledger)
        repository = SqliteKnowledgeRepository(restored.target)
    # Then
    removed_view = repository.files.root / first_relative
    assert not removed_view.exists() or "SECRET-ALICE-REMOVED" not in removed_view.read_text()
    if stage == "retract":
        assert canonical_path.exists()
        assert canonical_path.read_bytes() == first_memory.body
        clean = repository.read_memory(editor, first_id)
        assert clean is not None
        dispatched = MemoryViewDispatcher(repository, editor).dispatch_target(
            first_id,
            clean.revision.revision_id,
        )
        assert dispatched.completed
        clean_view_body = removed_view.read_bytes()
        _ = service.retract(
            RetractionRequest(
                operation_id="retract.user",
                actor=editor,
                target=target,
                reason_code="test",
                occurred_at=datetime.now(UTC),
            )
        )
        assert removed_view.read_bytes() == clean_view_body
    else:
        assert not canonical_path.exists()
    other_view = repository.files.root / second_relative
    if stage == "restore":
        surviving = repository.read_memory(second.trusted_context.actor, second_id)
        assert surviving is not None
        assert "BOB-SURVIVING" in surviving.body.decode()
        _ = MemoryViewDispatcher(repository, editor).dispatch_once()
        assert not removed_view.exists() or "SECRET-ALICE-REMOVED" not in removed_view.read_text()
    else:
        assert other_view.exists()
        assert "BOB-SURVIVING" in other_view.read_text()


def _materialize(
    repository: SqliteKnowledgeRepository, actor: ActorContext, document_id: str
) -> StoredMemory:
    stored = repository.read_memory(actor, document_id)
    assert stored is not None
    result = MemoryViewDispatcher(repository, actor).dispatch_target(
        document_id,
        stored.revision.revision_id,
    )
    assert result.completed, result
    return stored

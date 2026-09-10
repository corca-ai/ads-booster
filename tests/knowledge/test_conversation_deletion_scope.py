from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.backup import create_backup
from ads_booster.knowledge.contracts import AccessScope, GrantCapability, ScopeKind
from ads_booster.knowledge.deletion import DeletionService, PurgeRequest
from ads_booster.knowledge.erase_ledger import EraseLedger, EraseTarget
from ads_booster.knowledge.file_paths import RevisionFileDraft, SourceFileKind, SourceRevisionTarget
from ads_booster.knowledge.repository import (
    IndexOutboxItem,
    JobRegistration,
    MembershipRole,
    SourceRegistration,
    SqliteKnowledgeRepository,
)
from ads_booster.knowledge.repository_tool_state import RepositoryToolState
from ads_booster.knowledge.restore import restore_backup
from tests.knowledge.change_test_fixtures import actor
from tests.knowledge.test_backup_restore import NoReplicas
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input

if TYPE_CHECKING:
    from pathlib import Path

    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


@pytest.mark.parametrize("restored", [False, True])
def test_scrub_canonical_events_preserves_same_source_id_in_other_workspace(
    curation_input: CurationInput,
    tmp_path: Path,
    restored: bool,
) -> None:
    repository, processor, job, event, receipt = curation_input
    scope = AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.other")
    other = processor.actor.model_copy(
        update={
            "workspace_id": scope.workspace_id,
            "actor_id": "actor.other",
            "member_id": "member.other",
            "session_id": "session.other",
            "conversation_scope": scope,
            "grants": tuple(
                grant.model_copy(
                    update={
                        "workspace_id": scope.workspace_id,
                        "scope": scope,
                        "grant_id": f"other.{grant.grant_id}",
                    }
                )
                for grant in processor.actor.grants
            ),
        }
    )
    repository.register_actor(other, MembershipRole.ADMIN)
    original = repository.read_source(processor.actor, receipt.source_id)
    assert original is not None
    other_event = event.model_copy(
        update={
            "scope": scope,
            "speaker_ref": other.actor_id,
            "text": "Other workspace private fact",
        }
    )
    body = other_event.model_dump_json().encode()
    source = original.source.model_copy(
        update={
            "workspace_id": scope.workspace_id,
            "scope": scope,
            "owner_ref": other.member_id,
            "revision_id": "revision.other",
            "sha256": sha256(body).hexdigest(),
            "byte_length": len(body),
        }
    )
    _ = repository.register_source(
        SourceRegistration(
            operation_id="operation.other",
            payload_sha256=contract_sha256(source),
            source=source,
            segments=(),
            receipt=receipt.model_copy(
                update={
                    "delivery_id": "delivery.other",
                    "source_revision_id": source.revision_id,
                    "curation_job_id": "job.other",
                    "index_operation_id": "index.other",
                }
            ),
            job=JobRegistration(
                job=job.model_copy(
                    update={
                        "workspace_id": scope.workspace_id,
                        "scope": scope,
                        "job_id": "job.other",
                    }
                ),
                unique_key="other.curation",
            ),
            index_item=IndexOutboxItem(
                item_id="index.other",
                workspace_id=scope.workspace_id,
                entity_kind="source",
                entity_id=source.source_id,
                revision_id=source.revision_id,
            ),
            prepared_files=(
                repository.files.prepare(
                    RevisionFileDraft(
                        operation_id="operation.other",
                        target=SourceRevisionTarget(
                            source_id=source.source_id,
                            revision_id=source.revision_id,
                            file_kind=SourceFileKind.ORIGINAL,
                        ),
                        content=body,
                        sha256=source.sha256,
                    )
                ),
            ),
            conversation_event=other_event,
        )
    )
    assert (
        RepositoryToolState(repository).read_canonical_event(other, event.message_id) == other_event
    )
    editor = actor(
        capabilities=(GrantCapability.READ, GrantCapability.WRITE, GrantCapability.PURGE)
    )
    repository.register_actor(editor, MembershipRole.ADMIN)
    ledger = EraseLedger(tmp_path / "control")
    _ = ledger.initialize()
    backup = create_backup(
        repository,
        tmp_path / "backup",
        workspace_id=editor.workspace_id,
        erase_sequence=0,
        erase_head_sha256="0" * 64,
    )
    _ = DeletionService(repository, ledger, NoReplicas()).request_purge(
        PurgeRequest(
            request_id="purge.workspace",
            actor=editor,
            target=EraseTarget(kind="source", entity_id=receipt.source_id),
            reason_code="scope_test",
            explicit_admin_request=True,
            occurred_at=event.created_at,
        )
    )
    surviving_source = repository.read_source(other, source.source_id)
    assert surviving_source is not None
    assert surviving_source.body == body
    if restored:
        restored_receipt = restore_backup(
            backup.path, tmp_path / "restored", erase_authority=ledger
        )
        repository = SqliteKnowledgeRepository(restored_receipt.target)
    assert (
        RepositoryToolState(repository).read_canonical_event(other, event.message_id) == other_event
    )

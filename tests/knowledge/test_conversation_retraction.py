from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ads_booster.knowledge.backup import create_backup
from ads_booster.knowledge.contracts import (
    ConversationEvent,
    ConversationEventKind,
    EvidenceResolutionError,
    GrantCapability,
)
from ads_booster.knowledge.curation_contracts import CurationMemoryIntent
from ads_booster.knowledge.curation_memory import CurationMemoryWriter
from ads_booster.knowledge.deletion import DeletionService, PurgeRequest, RetractionRequest
from ads_booster.knowledge.erase_ledger import EraseLedger, EraseTarget
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import JobClaim, MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.repository_tool_state import RepositoryToolState, ToolStateError
from ads_booster.knowledge.restore import restore_backup
from ads_booster.knowledge.tool_contracts import ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.change_test_fixtures import actor
from tests.knowledge.test_backup_restore import NoReplicas
from tests.knowledge.test_curation_inputs import curation_input as fixture_curation_input
from tests.knowledge.test_curation_inputs import envelope

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.knowledge.batch_curation import CurationBatchWork
    from tests.knowledge.test_curation_inputs import CurationInput

curation_input = fixture_curation_input


@pytest.mark.parametrize("edited", [False, True])
@pytest.mark.parametrize("stage", ["retract", "purge", "restore"])
def test_message_source_removal_blocks_its_memory_and_retains_independent_fact(
    curation_input: CurationInput, tmp_path: Path, stage: str, edited: bool
) -> None:
    repository, processor, job, event, receipt = curation_input
    work = processor.build_curation_work(job)
    writer = CurationMemoryWriter(repository, ToolHost(repository))
    assert (
        writer.write(
            work.request,
            CurationMemoryIntent(
                subject_key="removed", text="31000", evidence_ids=(event.message_id,)
            ),
            work.trusted_context,
        ).status
        is ToolResultStatus.APPLIED
    )
    independent, correction = independent_work(curation_input)
    assert (
        writer.write(
            independent.request,
            CurationMemoryIntent(
                subject_key="survivor", text="41000", evidence_ids=("message.correction",)
            ),
            independent.trusted_context,
        ).status
        is ToolResultStatus.APPLIED
    )
    before = repository.read_memory(processor.actor, "memory.core")
    assert before is not None
    removed = next(entry for entry in before.entries if entry.text.startswith("removed:"))
    survivor = next(entry for entry in before.entries if entry.text.startswith("survivor:"))
    editor = actor(
        capabilities=(GrantCapability.READ, GrantCapability.WRITE, GrantCapability.PURGE)
    )
    repository.register_actor(editor, MembershipRole.ADMIN)
    if edited:
        revision = event.model_copy(
            update={
                "revision": 2,
                "event_kind": ConversationEventKind.MESSAGE_EDITED,
                "edited_at": correction.created_at + timedelta(seconds=1),
                "text": "Edited 51000",
            }
        )
        _ = KnowledgeIngestion(repository).ingest(
            editor,
            revision,
            envelope(revision, "delivery.edit"),
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
    target = EraseTarget(kind="source", entity_id=receipt.source_id)
    _ = service.retract(
        RetractionRequest(
            operation_id="retract.message",
            actor=editor,
            target=target,
            reason_code="test",
            occurred_at=work.trusted_context.invoked_at,
        )
    )
    if stage != "retract":
        _ = service.request_purge(
            PurgeRequest(
                request_id="purge.message",
                actor=editor,
                target=target,
                reason_code="test",
                explicit_admin_request=True,
                occurred_at=work.trusted_context.invoked_at,
            )
        )
    if stage == "restore":
        restored = restore_backup(backup.path, tmp_path / "restored", erase_authority=ledger)
        repository = SqliteKnowledgeRepository(restored.target)
    after = repository.read_memory(editor, "memory.core")
    assert after is not None
    assert after.entries == (survivor,)
    assert removed.text not in after.body.decode()
    assert repository.read_memory(editor, "memory.core", before.revision.revision_id) is None
    with pytest.raises(EvidenceResolutionError):
        _ = repository.resolve_evidence(editor, removed.source_refs[0])
    with pytest.raises(ToolStateError, match="authenticated_event_not_found"):
        _ = RepositoryToolState(repository).read_canonical_event(editor, event.message_id)
    replay = CurationMemoryWriter(repository, ToolHost(repository)).write(
        work.request,
        CurationMemoryIntent(subject_key="removed", text="31000", evidence_ids=(event.message_id,)),
        work.trusted_context,
    )
    assert replay.status is ToolResultStatus.REJECTED
    assert replay.error_code == "authenticated_event_not_found"
    resolved = repository.resolve_evidence(editor, survivor.source_refs[0])
    assert isinstance(resolved, ConversationEvent)
    assert resolved.text == correction.text
    if stage != "retract":
        with repository.connection() as connection:
            rows = TypeAdapter(tuple[tuple[str], ...]).validate_python(
                connection.execute(
                    "SELECT event_json FROM conversation_events WHERE message_id=?",
                    (event.message_id,),
                ).fetchall()
            )
        assert len(rows) == (2 if edited else 1)
        assert all(row[0] == '{"redacted":true}' for row in rows)


def independent_work(curation_input: CurationInput) -> tuple[CurationBatchWork, ConversationEvent]:
    repository, processor, _, event, _ = curation_input
    correction = event.model_copy(
        update={
            "message_id": "message.correction",
            "text": "Independent fact 41000",
            "created_at": event.created_at + timedelta(seconds=1),
        }
    )
    ingested = KnowledgeIngestion(repository).ingest(
        processor.actor,
        correction,
        envelope(correction, "delivery.correction"),
    )
    while True:
        lease = repository.claim_job(
            JobClaim(
                "worker.correction",
                correction.created_at,
                correction.created_at + timedelta(minutes=1),
            )
        )
        assert lease is not None
        if lease.job.job_id == ingested.unit_receipts[0].receipt.curation_job_id:
            independent = processor.build_curation_work(lease.job)
            break
    return independent, correction

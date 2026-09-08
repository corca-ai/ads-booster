from __future__ import annotations

from typing import TYPE_CHECKING

from ads_booster.knowledge.change_publication import ChangeGroup, ChangePublisher, MemoryPublication
from ads_booster.knowledge.contracts import (
    DependencyState,
    EvidenceKind,
    EvidenceRef,
    InstructionAuthority,
    KnowledgeOperation,
    KnowledgeOperationKind,
    MemoryKind,
    MemoryRevision,
    Provenance,
)
from ads_booster.knowledge.memory import MemorySnapshot
from ads_booster.knowledge.pages import PageChangeSet, PageSnapshot
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from tests.knowledge.change_test_fixtures import (
    NOW,
    WORKSPACE_SCOPE,
    actor,
    claim,
    digest,
    memory_document,
    memory_entry,
    memory_snapshot_parts,
    page_snapshot,
    register_evidence_source,
)
from tests.knowledge.test_changes import _memory_operation

if TYPE_CHECKING:
    from pathlib import Path


def test_team_semantic_update_fences_dependent_current_wiki_claim(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    _, event_ref = register_evidence_source(
        repository, body=b"Report blockers first.", conversation=True
    )
    assert event_ref is not None
    document = memory_document(kind=MemoryKind.TEAM, document_id="memory.team")
    entry = memory_entry(document=document).model_copy(update={"source_refs": (event_ref,)})
    document, revision, entries, body = memory_snapshot_parts(
        document=document, entries=(entry,)
    )
    publisher = ChangePublisher(repository)
    create_memory_id = "operation.reverse.memory-create"
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(
            operation_id=create_memory_id,
            memory_operations=(
                _memory_operation(
                    operation_id=create_memory_id,
                    document_id=document.document_id,
                    expected_revision_id="none",
                ),
            ),
        ),
        pages=None,
        memories=(MemoryPublication(MemorySnapshot(document, revision, entries, body)),),
        at=NOW,
    )
    memory_ref = EvidenceRef(
        evidence_kind=EvidenceKind.MEMORY_ENTRY,
        evidence_id=entry.entry_id,
        revision_id=revision.revision_id,
        scope=WORKSPACE_SCOPE,
        instruction_authority=InstructionAuthority.DATA,
        provenance=Provenance.AGENT_DERIVED,
    )
    dependent = claim(evidence_ref=memory_ref)
    page, page_revision, page_body = page_snapshot(claims=(dependent,))
    create_page = KnowledgeOperation(
        operation_id="operation.reverse.page-create",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(page.page_id,),
        expected_revision_ids=("none",),
        claim_ids=(dependent.claim_id,),
        reason="Explain the TEAM rule in Wiki.",
    )
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(operation_id=create_page.operation_id, page_operation=create_page),
        pages=PageChangeSet({page.page_id: PageSnapshot(page, page_revision, page_body)}, {}),
        memories=(),
        at=NOW,
    )
    changed = entry.model_copy(update={"text": "Report outcomes first."})
    changed_body = b"# Team\nReport outcomes first.\n"
    changed_revision = MemoryRevision(
        document_id=document.document_id,
        revision_id="memory.team.changed",
        previous_revision_id=revision.revision_id,
        body_sha256=digest(changed_body.decode()),
        entry_ids=(changed.entry_id,),
        created_at=NOW,
    )
    change_id = "operation.reverse.memory-change"

    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(
            operation_id=change_id,
            memory_operations=(
                _memory_operation(
                    operation_id=change_id,
                    document_id=document.document_id,
                    expected_revision_id=revision.revision_id,
                ),
            ),
        ),
        pages=None,
        memories=(
            MemoryPublication(
                MemorySnapshot(
                    document.model_copy(
                        update={"head_revision_id": changed_revision.revision_id}
                    ),
                    changed_revision,
                    (changed,),
                    changed_body,
                )
            ),
        ),
        at=NOW,
    )

    current = repository.read_page(editor, page.page_id)
    historical = repository.read_page(editor, page.page_id, page_revision.revision_id)
    state = repository.claim_dependency_state(
        editor, dependent.claim_id, page_revision.revision_id
    )
    assert state is DependencyState.STALE
    assert current is not None
    assert current.revision.claims == ()
    assert historical is not None
    assert historical.revision.claims == (dependent,)

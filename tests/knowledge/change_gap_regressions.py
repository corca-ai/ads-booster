from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.change_publication import ChangeGroup, ChangePublisher, MemoryPublication
from ads_booster.knowledge.change_validation import ChangeValidationError
from ads_booster.knowledge.contracts import (
    ClaimKind,
    KnowledgeOperation,
    KnowledgeOperationKind,
    MemoryRevision,
)
from ads_booster.knowledge.errors import PolicyEpochStaleError
from ads_booster.knowledge.memory import MemorySnapshot, handover_direct_entry_to_wiki
from ads_booster.knowledge.pages import PageChangeSet, PageSnapshot
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from tests.knowledge.change_test_fixtures import (
    NOW,
    actor,
    claim,
    digest,
    memory_document,
    memory_entry,
    memory_snapshot_parts,
    page_snapshot,
    register_evidence_source,
    wiki_summary_ref,
)
from tests.knowledge.test_changes import _memory_operation

if TYPE_CHECKING:
    from pathlib import Path


def test_page_rename_rejects_unrelated_claim_removal(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    source_ref, _ = register_evidence_source(repository, body=b"Evidence for both claims.")
    first = claim(claim_id="claim.keep", evidence_ref=source_ref)
    second = claim(claim_id="claim.unrelated", evidence_ref=source_ref)
    page, revision, body = page_snapshot(claims=(first, second))
    publisher = ChangePublisher(repository)
    create = KnowledgeOperation(
        operation_id="operation.rename.create",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(page.page_id,),
        expected_revision_ids=("none",),
        claim_ids=(first.claim_id, second.claim_id),
        reason="Create a page with two claims.",
    )
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(operation_id=create.operation_id, page_operation=create),
        pages=PageChangeSet({page.page_id: PageSnapshot(page, revision, body)}, {}),
        memories=(),
        at=NOW,
    )
    changed_revision = revision.model_copy(
        update={
            "revision_id": "page.pricing.rename",
            "previous_revision_id": revision.revision_id,
            "title": "Launch pricing",
            "aliases": (page.title,),
            "claims": (first,),
        }
    )
    changed_page = page.model_copy(
        update={
            "title": changed_revision.title,
            "aliases": changed_revision.aliases,
            "current_revision_id": changed_revision.revision_id,
        }
    )
    rename = KnowledgeOperation(
        operation_id="operation.rename.invalid",
        kind=KnowledgeOperationKind.PAGE_RENAME,
        target_page_ids=(page.page_id,),
        expected_revision_ids=(revision.revision_id,),
        reason="Rename without changing claims.",
    )

    with pytest.raises(ChangeValidationError, match="page_rename_changed_content"):
        _ = publisher.publish(
            actor=editor,
            group=ChangeGroup(operation_id=rename.operation_id, page_operation=rename),
            pages=PageChangeSet(
                {page.page_id: PageSnapshot(changed_page, changed_revision, body)}, {}
            ),
            memories=(),
            at=NOW,
        )
    stored = repository.read_page(editor, page.page_id)
    assert stored is not None
    assert stored.revision.claims == (first, second)


def test_atomic_direct_handover_resolves_new_wiki_claim(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    _, event_ref = register_evidence_source(
        repository, body=b"The launch price is 29 dollars.", conversation=True
    )
    assert event_ref is not None
    document = memory_document()
    direct = memory_entry(document=document).model_copy(update={"source_refs": (event_ref,)})
    document, revision, entries, body = memory_snapshot_parts(document=document, entries=(direct,))
    publisher = ChangePublisher(repository)
    create_id = "operation.handover.memory-create"
    _ = publisher.publish(
        actor=editor,
        group=ChangeGroup(
            operation_id=create_id,
            memory_operations=(
                _memory_operation(
                    operation_id=create_id,
                    document_id=document.document_id,
                    expected_revision_id="none",
                ),
            ),
        ),
        pages=None,
        memories=(MemoryPublication(MemorySnapshot(document, revision, entries, body)),),
        at=NOW,
    )
    wiki_claim = claim(kind=ClaimKind.FACT, evidence_ref=event_ref)
    page, page_revision, page_body = page_snapshot(claims=(wiki_claim,))
    summary = handover_direct_entry_to_wiki(
        entry=direct,
        wiki_ref=wiki_summary_ref(source_claim=wiki_claim),
        text="Launch price: 29 dollars.",
    )
    summary_body = b"# Memory\nLaunch price: 29 dollars.\n"
    summary_revision = MemoryRevision(
        document_id=document.document_id,
        revision_id="memory.core.handover",
        previous_revision_id=revision.revision_id,
        body_sha256=digest(summary_body.decode()),
        entry_ids=(summary.entry_id,),
        created_at=NOW,
    )
    operation_id = "operation.handover.atomic"
    page_operation = KnowledgeOperation(
        operation_id=operation_id,
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(page.page_id,),
        expected_revision_ids=("none",),
        claim_ids=(wiki_claim.claim_id,),
        reason="Move direct memory ownership to Wiki atomically.",
    )
    receipt = publisher.publish(
        actor=editor,
        group=ChangeGroup(
            operation_id=operation_id,
            page_operation=page_operation,
            memory_operations=(
                _memory_operation(
                    operation_id=operation_id,
                    document_id=document.document_id,
                    expected_revision_id=revision.revision_id,
                ),
            ),
        ),
        pages=PageChangeSet({page.page_id: PageSnapshot(page, page_revision, page_body)}, {}),
        memories=(
            MemoryPublication(
                MemorySnapshot(
                    document.model_copy(update={"head_revision_id": summary_revision.revision_id}),
                    summary_revision,
                    (summary,),
                    summary_body,
                )
            ),
        ),
        at=NOW,
    )

    stored_page = repository.read_page(editor, page.page_id)
    stored_memory = repository.read_memory(editor, document.document_id)
    assert receipt.resulting_revision_ids == (
        page_revision.revision_id,
        summary_revision.revision_id,
    )
    assert stored_page is not None
    assert stored_memory is not None
    assert stored_memory.entries == (summary,)


def test_stale_actor_cannot_publish_empty_page(tmp_path: Path) -> None:
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    stale = actor()
    repository.register_actor(stale, MembershipRole.ADMIN)
    current = stale.model_copy(
        update={
            "policy_epoch": 4,
            "grants": tuple(
                grant.model_copy(update={"policy_epoch": 4}) for grant in stale.grants
            ),
        }
    )
    repository.register_actor(current, MembershipRole.ADMIN)
    page, revision, body = page_snapshot(claims=())
    operation = KnowledgeOperation(
        operation_id="operation.stale.empty-page",
        kind=KnowledgeOperationKind.PAGE_CREATE,
        target_page_ids=(page.page_id,),
        expected_revision_ids=("none",),
        reason="Reject a stale actor before preparing an empty page.",
    )

    empty_revision = revision.model_copy(update={"claims": ()})
    with pytest.raises(PolicyEpochStaleError, match="stale_policy_epoch"):
        _ = ChangePublisher(repository).publish(
            actor=stale,
            group=ChangeGroup(operation_id=operation.operation_id, page_operation=operation),
            pages=PageChangeSet(
                {page.page_id: PageSnapshot(page, empty_revision, body)},
                {},
            ),
            memories=(),
            at=NOW,
        )
    assert repository.read_page(current, page.page_id) is None
    assert not tuple((tmp_path / "knowledge" / "staging").rglob("*.prepared"))

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.changes import ChangeGroup, ChangePublisher, MemoryPublication
from ads_booster.knowledge.contracts import (
    MemoryEntryKind,
    MemoryKind,
    MemoryOperation,
    MemoryOperationKind,
)
from ads_booster.knowledge.indexing import KnowledgeIndexWorker
from ads_booster.knowledge.memory import MemorySnapshot
from ads_booster.knowledge.repository import MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.retrieval import KnowledgeRetriever, SearchCorpus, SearchRequest
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


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize(
    ("seconds_after_expiry", "historical", "expected_visible"),
    [(-1, False, True), (0, False, False), (1, False, False), (1, True, True)],
)
def test_memory_search_enforces_expiry_at_query_time(
    tmp_path: Path,
    indexed: bool,
    seconds_after_expiry: int,
    historical: bool,
    expected_visible: bool,
) -> None:
    # Given: a published DAILY entry whose expiry has not been consolidated into its status.
    repository = SqliteKnowledgeRepository(tmp_path / "knowledge")
    editor = actor()
    repository.register_actor(editor, MembershipRole.ADMIN)
    source, _ = register_evidence_source(repository, body=b"Cedar packaging completed.")
    document = memory_document(
        kind=MemoryKind.DAILY, document_id="memory.daily.expiry", local_date=NOW.date()
    )
    expiry = NOW + timedelta(minutes=1)
    entry = memory_entry(document=document, kind=MemoryEntryKind.FACT).model_copy(
        update={
            "text": "Cedar packaging completed.",
            "source_refs": (source,),
            "expires_at": expiry,
        }
    )
    snapshot = MemorySnapshot(*memory_snapshot_parts(document=document, entries=(entry,)))
    operation = MemoryOperation(
        operation_id="operation.daily.expiry",
        kind=MemoryOperationKind.UPDATE,
        document_id=document.document_id,
        entry_id=entry.entry_id,
        expected_revision_id="none",
        reason="Preserve the daily result until its explicit expiry.",
        evidence_refs=(source.evidence_id,),
    )
    _ = ChangePublisher(repository).publish(
        actor=editor,
        group=ChangeGroup(operation_id=operation.operation_id, memory_operations=(operation,)),
        pages=None,
        memories=(MemoryPublication(snapshot),),
        at=NOW,
    )
    if indexed:
        worker = KnowledgeIndexWorker(repository)
        assert worker.run_once(editor.workspace_id, "worker.expiry", NOW).state == "indexed"
        assert worker.run_once(editor.workspace_id, "worker.expiry", NOW).state == "waiting"

    # When: search runs at the caller's explicit clock, independently of maintenance.
    result = KnowledgeRetriever(repository).search(
        editor,
        SearchRequest(query="packaging", corpus=SearchCorpus.MEMORY, historical=historical),
        now=expiry + timedelta(seconds=seconds_after_expiry),
    )

    # Then: ordinary search excludes expired memory; explicit history remains inspectable.
    assert bool(result.hits) is expected_visible

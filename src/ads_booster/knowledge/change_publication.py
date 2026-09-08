from __future__ import annotations

# ruff: noqa: D107, TC001
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.adoption_contracts import AdoptionReceiptResolver
from ads_booster.knowledge.change_actor_validation import require_current_actor
from ads_booster.knowledge.change_evidence import RepositoryEvidenceResolver
from ads_booster.knowledge.change_group_validation import require_group_bindings
from ads_booster.knowledge.change_invalidation import derive_memory_invalidations
from ads_booster.knowledge.change_page_validation import require_page_operation_semantics
from ads_booster.knowledge.change_reverse_invalidation import (
    derive_wiki_dependency_invalidations,
)
from ads_booster.knowledge.change_soul_validation import require_soul_adoptions
from ads_booster.knowledge.change_write_preparation import (
    prepare_memory_write,
    prepare_page_write,
)
from ads_booster.knowledge.governance_contracts import ConstraintBinding
from ads_booster.knowledge.memory import (
    MemorySnapshot,
    validate_memory_snapshot,
)
from ads_booster.knowledge.operation_contracts import (
    KnowledgeOperation,
    MemoryOperation,
    OperationReceipt,
)
from ads_booster.knowledge.operation_enums import OperationStatus
from ads_booster.knowledge.pages import PageChangeSet
from ads_booster.knowledge.repository import SqliteKnowledgeRepository
from ads_booster.knowledge.repository_types import (
    CatalogCommit,
    IndexOutboxItem,
    PageRedirect,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.scope_contracts import ActorContext


@dataclass(frozen=True, slots=True)
class ChangeGroup:
    operation_id: str
    page_operation: KnowledgeOperation | None = None
    memory_operations: tuple[MemoryOperation, ...] = ()
    adoption_receipt_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryPublication:
    snapshot: MemorySnapshot
    constraints: tuple[ConstraintBinding, ...] = ()


class ChangePublisher:
    def __init__(
        self,
        repository: SqliteKnowledgeRepository,
        adoption_resolver: AdoptionReceiptResolver | None = None,
    ) -> None:
        self._repository: SqliteKnowledgeRepository = repository
        self._adoption_resolver: AdoptionReceiptResolver | None = adoption_resolver

    def publish(
        self,
        *,
        actor: ActorContext,
        group: ChangeGroup,
        pages: PageChangeSet | None,
        memories: tuple[MemoryPublication, ...],
        at: datetime,
    ) -> OperationReceipt:
        page_snapshots = () if pages is None else tuple(pages.current.values())
        require_current_actor(
            repository=self._repository,
            actor=actor,
            pages=page_snapshots,
            memories=memories,
        )
        require_page_operation_semantics(
            repository=self._repository,
            actor=actor,
            operation=group.page_operation,
            pages=page_snapshots,
        )
        excluded_document_ids = frozenset(
            publication.snapshot.document.document_id for publication in memories
        )
        invalidations = derive_memory_invalidations(
            repository=self._repository,
            actor=actor,
            pages=page_snapshots,
            operation_id=group.operation_id,
            at=at,
            excluded_document_ids=excluded_document_ids,
        )
        wiki_invalidations = derive_wiki_dependency_invalidations(
            repository=self._repository,
            actor=actor,
            memories=memories,
        )
        memories = (
            *memories,
            *(MemoryPublication(item.snapshot) for item in invalidations),
        )
        group = ChangeGroup(
            operation_id=group.operation_id,
            page_operation=group.page_operation,
            memory_operations=(
                *group.memory_operations,
                *(item.operation for item in invalidations),
            ),
            adoption_receipt_ids=group.adoption_receipt_ids,
        )
        require_group_bindings(group, page_snapshots, memories)
        require_soul_adoptions(
            actor=actor,
            memories=memories,
            receipt_ids=group.adoption_receipt_ids,
            resolver=self._adoption_resolver,
        )
        evidence = RepositoryEvidenceResolver(
            self._repository,
            pages=page_snapshots,
            memories=tuple(item.snapshot for item in memories),
        )
        for snapshot in page_snapshots:
            evidence.validate_page(actor, snapshot, at)
        for publication in memories:
            catalog = evidence.validation_catalog(
                actor=actor,
                entries=publication.snapshot.entries,
                brand_id=publication.snapshot.document.brand_id,
            )
            validate_memory_snapshot(
                snapshot=publication.snapshot,
                actor=actor,
                catalog=catalog,
                at=at,
            )
        page_writes = tuple(
            prepare_page_write(self._repository, group.operation_id, snapshot)
            for snapshot in page_snapshots
        )
        memory_writes = tuple(
            prepare_memory_write(
                self._repository,
                group.operation_id,
                publication.snapshot,
                publication.constraints,
            )
            for publication in memories
        )
        resulting = tuple(snapshot.revision.revision_id for snapshot in page_snapshots) + tuple(
            item.snapshot.revision.revision_id for item in memories
        )
        receipt = OperationReceipt(
            schema="knowledge.operation-receipt.v1",
            operation_id=group.operation_id,
            status=OperationStatus.APPLIED,
            resulting_revision_ids=resulting,
            retryable=False,
            occurred_at=at,
        )
        records = (() if group.page_operation is None else (group.page_operation,)) + tuple(
            group.memory_operations
        )
        payload: JsonObject = {
            "records": [record.model_dump(mode="json") for record in records],
            "revisions": list(resulting),
            "adoption_receipt_ids": list(group.adoption_receipt_ids),
            "dependency_invalidations": [
                {
                    "upstream_kind": item.upstream_kind,
                    "upstream_id": item.upstream_id,
                    "upstream_revision_id": item.upstream_revision_id,
                    "resulting_state": item.resulting_state.value,
                    "reason": item.reason,
                }
                for item in wiki_invalidations
            ],
        }
        index_items = tuple(
            IndexOutboxItem(
                item_id=f"index.{group.operation_id}.{snapshot.page.page_id}",
                workspace_id=actor.workspace_id,
                entity_kind="page",
                entity_id=snapshot.page.page_id,
                revision_id=snapshot.revision.revision_id,
            )
            for snapshot in page_snapshots
        ) + tuple(
            IndexOutboxItem(
                item_id=f"index.{group.operation_id}.{item.snapshot.document.document_id}",
                workspace_id=actor.workspace_id,
                entity_kind="memory",
                entity_id=item.snapshot.document.document_id,
                revision_id=item.snapshot.revision.revision_id,
            )
            for item in memories
        )
        return self._repository.commit_catalog(
            CatalogCommit(
                operation_id=group.operation_id,
                actor=actor,
                payload_sha256=contract_sha256(payload),
                receipt=receipt,
                operation_records=records,
                page_writes=page_writes,
                memory_writes=memory_writes,
                index_items=index_items,
                redirects=()
                if pages is None
                else tuple(
                    PageRedirect(from_page_id=source, to_page_id=target)
                    for source, target in pages.redirects.items()
                ),
                dependency_invalidations=wiki_invalidations,
            )
        )


__all__ = ["ChangeGroup", "ChangePublisher", "MemoryPublication"]

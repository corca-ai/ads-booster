from __future__ import annotations

from hashlib import sha256

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.contracts import (
    ActorContext,
    AuthorityClass,
    AuthorityRef,
    Brand,
    BrandEvent,
    BrandEventKind,
    BrandState,
    MemoryDocument,
    MemoryKind,
    MemoryRevision,
    OperationReceipt,
    OperationStatus,
    ScopeKind,
)
from ads_booster.knowledge.file_store import MemoryRevisionTarget, RevisionFileDraft
from ads_booster.knowledge.repository import BrandRegistration, SqliteKnowledgeRepository
from tests.knowledge.change_test_fixtures import NOW


def brand_registration(
    repository: SqliteKnowledgeRepository,
    actor: ActorContext,
    brand_id: str,
    suffix: str,
) -> BrandRegistration:
    operation_id = f"operation.brand.{suffix}"
    document_id = f"memory.soul.{suffix}"
    revision_id = f"{document_id}.rev1"
    brand = Brand(
        brand_id=brand_id,
        workspace_id=actor.workspace_id,
        name="Shared Name",
        revision=1,
        state=BrandState.ACTIVE,
        scope=actor.conversation_scope
        if actor.conversation_scope.kind is ScopeKind.CHANNEL
        else None,
    )
    authority = AuthorityRef(
        event_id=f"event.brand.{suffix}",
        authority_class=AuthorityClass.RUNTIME_POLICY,
        actor_ref=actor.actor_id,
        workspace_id=actor.workspace_id,
        scope=actor.conversation_scope,
        policy_epoch=actor.policy_epoch,
    )
    event = BrandEvent(
        event_id=authority.event_id,
        kind=BrandEventKind.REGISTERED,
        brand_id=brand_id,
        workspace_id=actor.workspace_id,
        authority_ref=authority,
        occurred_at=NOW,
    )
    body = f"immutable {brand_id}".encode()
    revision = MemoryRevision(
        document_id=document_id,
        revision_id=revision_id,
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        created_at=NOW,
    )
    document = MemoryDocument(
        document_id=document_id,
        workspace_id=actor.workspace_id,
        kind=MemoryKind.SOUL,
        brand_id=brand_id,
        timezone="UTC",
        head_revision_id=revision_id,
        scope=actor.conversation_scope
        if actor.conversation_scope.kind is ScopeKind.CHANNEL
        else None,
    )
    prepared = repository.files.prepare(
        RevisionFileDraft(
            operation_id=operation_id,
            target=MemoryRevisionTarget(
                workspace_id=actor.workspace_id,
                document_id=document_id,
                revision_id=revision_id,
            ),
            content=body,
            sha256=revision.body_sha256,
        )
    )
    return BrandRegistration(
        brand=brand,
        event=event,
        document=document,
        revision=revision,
        prepared_file=prepared,
        receipt=OperationReceipt(
            schema="knowledge.operation-receipt.v1",
            operation_id=operation_id,
            status=OperationStatus.APPLIED,
            resulting_revision_ids=(revision_id,),
            retryable=False,
            occurred_at=NOW,
        ),
        payload_sha256=contract_sha256(event),
    )

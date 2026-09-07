from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
from ads_booster.knowledge.file_paths import MemoryRevisionTarget, RevisionFileDraft
from ads_booster.knowledge.governance_contracts import Brand, BrandEvent, TaskBinding
from ads_booster.knowledge.memory_contracts import MemoryDocument, MemoryRevision
from ads_booster.knowledge.operation_contracts import OperationReceipt
from ads_booster.knowledge.operation_enums import (
    BrandEventKind,
    BrandState,
    OperationStatus,
    TaskBindingState,
)
from ads_booster.knowledge.evidence_contracts import AuthorityRef
from ads_booster.knowledge.contract_types import AuthorityClass, MemoryKind
from ads_booster.knowledge.repository_types import BrandRegistration

from ads_booster.cli.knowledge_runtime import CliKnowledgeSession
from ads_booster.transport.json_types import JsonObject


def register_brand(session: CliKnowledgeSession, request: JsonObject) -> OperationReceipt:
    operation_id = str(request.get("operation_id") or request.get("request_id") or "")
    name = str(request.get("name") or "")
    if not operation_id or not name:
        raise ValueError("knowledge_brand_request_invalid")
    workspace_claim = request.get("workspace_id") or request.get("workspace_ref")
    if workspace_claim is not None and str(workspace_claim) != session.actor.workspace_id:
        raise ValueError("knowledge_brand_workspace_claim_rejected")
    brand_id = f"brand.{sha256(operation_id.encode()).hexdigest()[:32]}"
    now = datetime.now(UTC)
    authority = AuthorityRef(
        event_id=f"event.{operation_id}",
        authority_class=AuthorityClass.RUNTIME_POLICY,
        actor_ref=session.actor.actor_id,
        workspace_id=session.actor.workspace_id,
        scope=session.actor.conversation_scope,
        policy_epoch=session.actor.policy_epoch,
    )
    brand = Brand(
        brand_id=brand_id,
        workspace_id=session.actor.workspace_id,
        name=name,
        revision=1,
        state=BrandState.ACTIVE,
    )
    event = BrandEvent(
        event_id=authority.event_id,
        kind=BrandEventKind.REGISTERED,
        brand_id=brand_id,
        workspace_id=session.actor.workspace_id,
        authority_ref=authority,
        occurred_at=now,
    )
    body = b""
    document_id = f"memory.soul.{brand_id}"
    revision_id = f"{document_id}.initial"
    revision = MemoryRevision(
        document_id=document_id,
        revision_id=revision_id,
        previous_revision_id=None,
        body_sha256=sha256(body).hexdigest(),
        created_at=now,
    )
    document = MemoryDocument(
        document_id=document_id,
        workspace_id=session.actor.workspace_id,
        kind=MemoryKind.SOUL,
        brand_id=brand_id,
        timezone="UTC",
        head_revision_id=revision_id,
    )
    receipt = OperationReceipt(
        schema="knowledge.operation-receipt.v1",
        operation_id=operation_id,
        status=OperationStatus.APPLIED,
        resulting_revision_ids=(brand_id, revision_id),
        retryable=False,
        occurred_at=now,
    )
    prepared = session.repository.files.prepare(
        RevisionFileDraft(
            operation_id=operation_id,
            target=MemoryRevisionTarget(session.actor.workspace_id, document_id, revision_id),
            content=body,
            sha256=revision.body_sha256,
        )
    )
    return session.repository.register_brand(
        BrandRegistration(
            brand=brand,
            event=event,
            document=document,
            revision=revision,
            prepared_file=prepared,
            receipt=receipt,
            payload_sha256=contract_sha256(event),
        )
    )


def list_brands(session: CliKnowledgeSession) -> tuple[Brand, ...]:
    with session.repository.connection() as connection:
        rows = connection.execute(
            "SELECT brand_id FROM brands WHERE workspace_id=? ORDER BY brand_id",
            (session.actor.workspace_id,),
        ).fetchall()
    brands = tuple(session.repository.brand(session.actor, str(row[0])) for row in rows)
    return tuple(item for item in brands if item is not None)


def open_task(session: CliKnowledgeSession, request: JsonObject) -> TaskBinding:
    task_id = str(request.get("task_id") or request.get("request_id") or "")
    if not task_id:
        raise ValueError("knowledge_task_id_required")
    action = KnowledgeActionKind(str(request.get("action_kind", "team_chat")))
    brand_id = None if request.get("brand_id") is None else str(request["brand_id"])
    brand = None if brand_id is None else session.repository.brand(session.actor, brand_id)
    if brand_id is not None and brand is None:
        raise ValueError("knowledge_brand_not_found")
    binding = TaskBinding(
        task_id=task_id,
        workspace_id=session.actor.workspace_id,
        actor_ref=session.actor.actor_id,
        member_id=session.actor.member_id,
        session_id=session.actor.session_id,
        action_kind=action,
        brand_id=brand_id,
        brand_catalog_revision=None if brand is None else brand.revision,
        capability_epoch=session.actor.policy_epoch,
        state=TaskBindingState.ACTIVE,
        opened_at=datetime.now(UTC),
    )
    return session.host.open_task(session.actor, binding)


__all__ = ["list_brands", "open_task", "register_brand"]

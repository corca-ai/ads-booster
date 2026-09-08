from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique
from typing import Annotated, Literal, Protocol, override

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import BoundedId, contract_sha256
from ads_booster.contracts.models import RelativePath, Sha256Digest
from ads_booster.knowledge.contract_types import KnowledgeContractModel, UtcDatetime
from ads_booster.knowledge.erase_ledger import EraseArtifact, EraseLedger, EraseTarget
from ads_booster.knowledge.scope_contracts import ActorContext


@unique
class PurgeState(StrEnum):
    PURGE_PENDING = "purge_pending"
    LOCAL_PURGED = "local_purged"
    PURGED = "purged"


@unique
class ReplicaDeletionState(StrEnum):
    RETAINED = "retained"
    PURGE_PENDING = "purge_pending"
    PURGED = "purged"


class RetractionRequest(KnowledgeContractModel):
    operation_id: BoundedId
    actor: ActorContext
    target: EraseTarget
    reason_code: Annotated[str, Field(min_length=1, max_length=160)]
    occurred_at: UtcDatetime


class RetractionReceipt(KnowledgeContractModel):
    operation_id: BoundedId
    workspace_id: BoundedId
    tombstone_id: BoundedId
    target: EraseTarget
    blocked_dependency_ids: Annotated[tuple[BoundedId, ...], Field(max_length=4096)] = ()
    blocked_transfer_ids: Annotated[tuple[BoundedId, ...], Field(max_length=4096)] = ()
    occurred_at: UtcDatetime


class PurgeRequest(KnowledgeContractModel):
    request_id: BoundedId
    actor: ActorContext
    target: EraseTarget
    reason_code: Annotated[str, Field(min_length=1, max_length=160)]
    explicit_admin_request: Literal[True]
    occurred_at: UtcDatetime

    @model_validator(mode="after")
    def require_target_workspace(self) -> PurgeRequest:
        if self.actor.workspace_id == "":
            raise PydanticCustomError("purge_workspace_missing", "purge requires a workspace")
        return self


class PurgeManifestEntry(KnowledgeContractModel):
    kind: Annotated[str, Field(min_length=1, max_length=80)]
    entity_id: BoundedId
    revision_id: BoundedId | None = None
    relative_path: RelativePath | None = None
    content_sha256: Sha256Digest | None = None

    def as_erase_artifact(self) -> EraseArtifact:
        return EraseArtifact(
            kind=self.kind,
            entity_id=self.entity_id,
            revision_id=self.revision_id,
            relative_path=self.relative_path,
            content_sha256=self.content_sha256,
        )


class PurgeManifest(KnowledgeContractModel):
    request_id: BoundedId
    workspace_id: BoundedId
    target: EraseTarget
    entries: Annotated[tuple[PurgeManifestEntry, ...], Field(max_length=4096)] = ()


class PurgeReceipt(KnowledgeContractModel):
    request_id: BoundedId
    workspace_id: BoundedId
    state: PurgeState
    ledger_sequence: Annotated[int, Field(ge=1)]
    ledger_entry_sha256: Sha256Digest
    local_pending: Annotated[int, Field(ge=0)]
    replica_pending: Annotated[int, Field(ge=0)]
    clean_revision_ids: Annotated[tuple[BoundedId, ...], Field(max_length=512)] = ()
    updated_at: UtcDatetime


class HistoryRedacted(KnowledgeContractModel):
    status: Literal["history_redacted"] = "history_redacted"
    entity_id: BoundedId
    revision_id: BoundedId
    current_revision_id: BoundedId | None = None


class ReplicaPurgeRequest(KnowledgeContractModel):
    request_id: BoundedId
    transfer_id: BoundedId
    system_id: BoundedId
    replica_id: BoundedId


class ReplicaPurgeReceipt(KnowledgeContractModel):
    receipt_id: BoundedId
    request_id: BoundedId
    transfer_id: BoundedId
    system_id: BoundedId
    replica_id: BoundedId
    state: Literal[ReplicaDeletionState.PURGE_PENDING, ReplicaDeletionState.PURGED]
    checked_at: UtcDatetime


class ReplicaPurgePort(Protocol):
    def purge(self, request: ReplicaPurgeRequest) -> ReplicaPurgeReceipt: ...


@dataclass(slots=True)
class DeletionError(Exception):
    code: str
    target_id: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: target={self.target_id}"


@dataclass(frozen=True, slots=True)
class DeletionService:
    repository: KnowledgeRepository
    ledger: EraseLedger
    replica_port: ReplicaPurgePort

    def retract(self, request: RetractionRequest) -> RetractionReceipt:
        from ads_booster.knowledge.repository_deletion import retract_target

        return retract_target(self.repository, request)

    def request_purge(self, request: PurgeRequest) -> PurgeReceipt:
        from ads_booster.knowledge.repository_deletion import (
            existing_purge_receipt,
            persist_purge_request,
            prepare_purge_manifest,
            require_purge_authority,
        )

        replay = existing_purge_receipt(self.repository, request.actor, request.request_id)
        if replay is not None:
            return replay
        require_purge_authority(self.repository, request)
        manifest = prepare_purge_manifest(self.repository, request)
        manifest_sha256 = contract_sha256(manifest)
        ledger_entry = self.ledger.append(
            workspace_id=request.actor.workspace_id,
            request_id=request.request_id,
            target=request.target,
            artifacts=tuple(item.as_erase_artifact() for item in manifest.entries),
            manifest_sha256=manifest_sha256,
            created_at=request.occurred_at,
        )
        persist_purge_request(
            self.repository,
            request,
            manifest,
            manifest_sha256,
            ledger_entry,
        )
        return self.reconcile_purge(request.actor, request.request_id)

    def purge_manifest(self, actor: ActorContext, request_id: str) -> PurgeManifest:
        from ads_booster.knowledge.repository_deletion import read_purge_manifest

        return read_purge_manifest(self.repository, actor, request_id)

    def history_redaction(
        self,
        actor: ActorContext,
        document_id: str,
        revision_id: str,
    ) -> HistoryRedacted | None:
        from ads_booster.knowledge.repository_deletion import memory_history_redaction

        redaction = memory_history_redaction(
            self.repository,
            actor.workspace_id,
            document_id,
            revision_id,
        )
        if redaction is None:
            return None
        _, current_revision_id = redaction
        return HistoryRedacted(
            entity_id=document_id,
            revision_id=revision_id,
            current_revision_id=current_revision_id,
        )

    def reconcile_purge(self, actor: ActorContext, request_id: str) -> PurgeReceipt:
        from ads_booster.knowledge.repository_deletion import (
            clean_mixed_memory_revisions,
            finish_purge_reconciliation,
            pending_replica_requests,
            purge_local_artifacts,
            record_replica_receipt,
            require_reconcile_authority,
        )

        require_reconcile_authority(self.repository, actor, request_id)
        clean_revision_ids = clean_mixed_memory_revisions(self.repository, actor, request_id)
        purge_local_artifacts(self.repository, request_id)
        for replica in pending_replica_requests(self.repository, request_id):
            receipt = self.replica_port.purge(replica)
            if (
                receipt.request_id != replica.request_id
                or receipt.transfer_id != replica.transfer_id
                or receipt.system_id != replica.system_id
                or receipt.replica_id != replica.replica_id
            ):
                raise DeletionError("replica_purge_receipt_binding_mismatch", replica.replica_id)
            record_replica_receipt(self.repository, receipt)
        return finish_purge_reconciliation(
            self.repository,
            request_id,
            clean_revision_ids,
            datetime.now(actor.authenticated_at.tzinfo),
        )


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ads_booster.knowledge.repository_protocol import KnowledgeRepository


__all__ = [
    "DeletionError",
    "DeletionService",
    "HistoryRedacted",
    "PurgeManifest",
    "PurgeManifestEntry",
    "PurgeReceipt",
    "PurgeRequest",
    "PurgeState",
    "ReplicaDeletionState",
    "ReplicaPurgePort",
    "ReplicaPurgeReceipt",
    "ReplicaPurgeRequest",
    "RetractionReceipt",
    "RetractionRequest",
]

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, override

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.grant_policy import authorize_write
from ads_booster.knowledge.ingestion_build import stable_id
from ads_booster.knowledge.repository_types import IndexOutboxItem, SourceAdmissionChange

if TYPE_CHECKING:
    from ads_booster.knowledge.curation_contracts import SourceDispositionIntent
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.tool_contracts import TrustedInvocationContext


@dataclass(frozen=True, slots=True)
class DispositionApplyRequest:
    intent: SourceDispositionIntent
    trusted_context: TrustedInvocationContext
    operation_id: str


@dataclass(frozen=True, slots=True)
class DispositionReceipt:
    operation_id: str
    source_id: str
    admission_revision: int
    disposition: str
    replayed: bool


class SourceDispositionPort(Protocol):
    def apply(self, request: DispositionApplyRequest) -> DispositionReceipt: ...


@dataclass(slots=True)
class SourceDispositionError(Exception):
    code: str

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class RepositorySourceDisposition:
    repository: SqliteKnowledgeRepository

    def apply(self, request: DispositionApplyRequest) -> DispositionReceipt:
        intent = request.intent
        context = request.trusted_context
        source = self.repository.read_source(context.actor, intent.source_id)
        if source is None:
            raise SourceDispositionError("curation_source_not_found")
        current = source.source
        capability = next(
            (
                item
                for item in context.source_capabilities
                if item.source_id == intent.source_id
                and item.revision_id == intent.revision_id
                and item.workspace_id == context.actor.workspace_id
                and item.actor_ref == context.actor.actor_id
                and (item.expires_at is None or context.invoked_at < item.expires_at)
            ),
            None,
        )
        if capability is None:
            raise SourceDispositionError("curation_source_capability_missing")
        if current.revision_id != intent.revision_id:
            raise SourceDispositionError("curation_source_revision_stale")
        _ = authorize_write(
            actor=context.actor,
            target_scope=current.scope,
            at=context.invoked_at,
        )
        payload = {
            "source_id": intent.source_id,
            "revision_id": intent.revision_id,
            "expected_admission_revision": intent.expected_admission_revision,
            "disposition": intent.disposition.value,
            "reason": intent.reason,
            "job_id": context.job_id,
        }
        updated = self.repository.change_source_admission(
            SourceAdmissionChange(
                operation_id=request.operation_id,
                payload_sha256=contract_sha256(payload),
                workspace_id=context.actor.workspace_id,
                source_id=intent.source_id,
                expected_admission_revision=intent.expected_admission_revision,
                disposition=intent.disposition,
                index_item=IndexOutboxItem(
                    item_id=stable_id("index", request.operation_id),
                    workspace_id=context.actor.workspace_id,
                    entity_kind="source",
                    entity_id=intent.source_id,
                    revision_id=intent.revision_id,
                    extraction_version=current.extractor_version,
                    admission_revision=intent.expected_admission_revision + 1,
                ),
                occurred_at=context.invoked_at,
            )
        )
        return DispositionReceipt(
            operation_id=request.operation_id,
            source_id=updated.source_id,
            admission_revision=updated.admission_revision,
            disposition=updated.disposition.value,
            replayed=current.admission_revision != intent.expected_admission_revision,
        )


__all__ = [
    "DispositionApplyRequest",
    "DispositionReceipt",
    "RepositorySourceDisposition",
    "SourceDispositionError",
    "SourceDispositionPort",
]

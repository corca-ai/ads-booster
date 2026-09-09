from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Literal, Never, Protocol, override

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.contracts.knowledge_context import KnowledgeContextTransfer
    from ads_booster.contracts.knowledge_selection import KnowledgeActionKind
    from ads_booster.contracts.models import Sha256Digest
    from ads_booster.knowledge.contracts import (
        AccessScope,
        ActorContext,
        Brand,
        BrandEvent,
        ConstraintBinding,
        ConversationEvent,
        DependencyState,
        IngestReceipt,
        KnowledgeJob,
        KnowledgeOperation,
        KnowledgeRevision,
        MemoryDocument,
        MemoryEntry,
        MemoryOperation,
        MemoryRevision,
        OperationReceipt,
        Source,
        SourceDisposition,
        SourceSegment,
        WikiPage,
    )
    from ads_booster.knowledge.file_store import PreparedRevisionFile
    from ads_booster.knowledge.operation_enums import JobState
    from ads_booster.knowledge.skill_contracts import SkillOperation, SkillRecord


@unique
class MembershipRole(StrEnum):
    READER = "reader"
    EDITOR = "editor"
    ADMIN = "admin"


@unique
class RunBindingState(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


@unique
class RepositoryCommitBoundary(StrEnum):
    BEFORE_FILE_PUBLISH = "before_file_publish"
    AFTER_FILE_PUBLISH = "after_file_publish"
    BEFORE_DB_COMMIT = "before_db_commit"
    AFTER_DB_COMMIT = "after_db_commit"


class CrashInjector(Protocol):
    def __call__(self, boundary: RepositoryCommitBoundary, /) -> None: ...


@dataclass(frozen=True, slots=True)
class RunBinding:
    binding_id: str
    run_id: str
    binding_revision: int
    workspace_id: str
    member_id: str
    session_id: str
    scope: AccessScope
    grant_set_sha256: Sha256Digest
    policy_epoch: int
    brand_id: str | None
    action_kind: KnowledgeActionKind | None
    state: RunBindingState


@dataclass(frozen=True, slots=True)
class HeadExpectation:
    entity_id: str
    expected_revision_id: str | None
    resulting_revision_id: str


@dataclass(frozen=True, slots=True)
class IndexOutboxItem:
    item_id: str
    workspace_id: str
    entity_kind: Literal["source", "page", "memory"]
    entity_id: str
    revision_id: str
    extraction_version: str | None = None
    admission_revision: int | None = None
    indexer_version: str = "indexer.v1"


@dataclass(frozen=True, slots=True)
class PageRevisionWrite:
    page: WikiPage
    revision: KnowledgeRevision
    expected: HeadExpectation
    prepared_file: PreparedRevisionFile


@dataclass(frozen=True, slots=True)
class MemoryRevisionWrite:
    document: MemoryDocument
    revision: MemoryRevision
    expected: HeadExpectation
    prepared_file: PreparedRevisionFile
    entries: tuple[MemoryEntry, ...] = ()
    constraints: tuple[ConstraintBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillRevisionWrite:
    operation: SkillOperation
    expected: HeadExpectation
    record: SkillRecord | None = None
    prepared_file: PreparedRevisionFile | None = None


@dataclass(frozen=True, slots=True)
class EvidenceDependencyInvalidation:
    upstream_kind: Literal["memory_entry"]
    upstream_id: str
    upstream_revision_id: str
    resulting_state: DependencyState
    reason: str


@dataclass(frozen=True, slots=True)
class JobRegistration:
    job: KnowledgeJob
    unique_key: str


type AuditRecord = KnowledgeOperation | MemoryOperation | SkillOperation


@dataclass(frozen=True, slots=True)
class CatalogCommit:
    operation_id: str
    actor: ActorContext
    payload_sha256: Sha256Digest
    receipt: OperationReceipt
    operation_records: tuple[AuditRecord, ...] = ()
    page_writes: tuple[PageRevisionWrite, ...] = ()
    memory_writes: tuple[MemoryRevisionWrite, ...] = ()
    skill_writes: tuple[SkillRevisionWrite, ...] = ()
    index_items: tuple[IndexOutboxItem, ...] = ()
    jobs: tuple[JobRegistration, ...] = ()
    redirects: tuple[PageRedirect, ...] = ()
    dependency_invalidations: tuple[EvidenceDependencyInvalidation, ...] = ()


@dataclass(frozen=True, slots=True)
class PageRedirect:
    from_page_id: str
    to_page_id: str


@dataclass(frozen=True, slots=True)
class BrandRegistration:
    brand: Brand
    event: BrandEvent
    document: MemoryDocument
    revision: MemoryRevision
    prepared_file: PreparedRevisionFile
    receipt: OperationReceipt
    payload_sha256: Sha256Digest


@dataclass(frozen=True, slots=True)
class SourceRegistration:
    operation_id: str
    payload_sha256: Sha256Digest
    source: Source
    segments: tuple[SourceSegment, ...]
    receipt: IngestReceipt
    job: JobRegistration
    index_item: IndexOutboxItem
    prepared_files: tuple[PreparedRevisionFile, ...]
    conversation_event: ConversationEvent | None = None
    observation: SourceObservationWrite | None = None


@dataclass(frozen=True, slots=True)
class SourceAdmissionChange:
    operation_id: str
    payload_sha256: Sha256Digest
    workspace_id: str
    source_id: str
    expected_admission_revision: int
    disposition: SourceDisposition
    index_item: IndexOutboxItem
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SourceObservationWrite:
    observation_id: str
    workspace_id: str
    source_id: str
    revision_id: str
    fetched_at: datetime
    final_url: str
    http_status: int
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class StoredPage:
    page: WikiPage
    revision: KnowledgeRevision
    body: bytes


@dataclass(frozen=True, slots=True)
class StoredMemory:
    document: MemoryDocument
    revision: MemoryRevision
    entries: tuple[MemoryEntry, ...]
    body: bytes


@dataclass(frozen=True, slots=True)
class StoredSource:
    source: Source
    segments: tuple[SourceSegment, ...]
    body: bytes


@dataclass(frozen=True, slots=True)
class StoredSkill:
    record: SkillRecord
    body: bytes
    display_pending: bool


@dataclass(frozen=True, slots=True)
class JobClaim:
    worker_id: str
    now: datetime
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class JobLease:
    job: KnowledgeJob
    worker_id: str
    lease_generation: int
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class JobCompletion:
    job_id: str
    worker_id: str
    lease_generation: int
    state: JobState
    result_sha256: Sha256Digest
    completed_at: datetime


@dataclass(slots=True)
class RepositoryConflictError(Exception):
    code: str
    target: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.target}"


def repository_error(code: str, target: str) -> RepositoryConflictError:
    return RepositoryConflictError(code, target)


def conflict(code: str, target: str) -> Never:
    raise repository_error(code, target)


@dataclass(frozen=True, slots=True)
class StoredTransfer:
    transfer: KnowledgeContextTransfer
    dependencies: tuple[tuple[str, str, str], ...]


__all__ = [
    "AuditRecord",
    "BrandRegistration",
    "CatalogCommit",
    "CrashInjector",
    "EvidenceDependencyInvalidation",
    "HeadExpectation",
    "IndexOutboxItem",
    "JobClaim",
    "JobCompletion",
    "JobLease",
    "JobRegistration",
    "MembershipRole",
    "MemoryRevisionWrite",
    "PageRedirect",
    "PageRevisionWrite",
    "RepositoryCommitBoundary",
    "RepositoryConflictError",
    "RunBinding",
    "RunBindingState",
    "SkillRevisionWrite",
    "SourceAdmissionChange",
    "SourceObservationWrite",
    "SourceRegistration",
    "StoredMemory",
    "StoredPage",
    "StoredSkill",
    "StoredSource",
    "StoredTransfer",
    "conflict",
    "repository_error",
]

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.knowledge.change_publication import ChangeGroup, ChangePublisher, MemoryPublication
from ads_booster.knowledge.change_validation import claim_semantic_fingerprint
from ads_booster.knowledge.contract_types import (
    ClaimStatus,
    DependencyState,
    EvidenceKind,
    InstructionAuthority,
    MemoryKind,
    MemoryOrigin,
    MemoryStatus,
    Provenance,
    ScopeKind,
    WikiPageStatus,
)
from ads_booster.knowledge.ingestion_build import stable_id
from ads_booster.knowledge.jobs import JobProcessResult
from ads_booster.knowledge.memory import MemorySnapshot
from ads_booster.knowledge.memory_consolidation_views import (
    MemoryViewDispatcher,
    MemoryViewDispatchResult,
    memory_maintenance_scope_keys,
    memory_maintenance_scopes,
)
from ads_booster.knowledge.memory_contracts import MemoryEntry, MemoryRevision
from ads_booster.knowledge.operation_contracts import KnowledgeJob, MemoryOperation
from ads_booster.knowledge.operation_enums import (
    JobKind,
    JobPriority,
    JobState,
    MemoryOperationKind,
)
from ads_booster.knowledge.repository_types import (
    JobLease,
    JobRegistration,
    RepositoryConflictError,
    StoredMemory,
)

if TYPE_CHECKING:
    from datetime import datetime
    from threading import Event

    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.scope_contracts import AccessScope, ActorContext

_HEAD_ROWS = TypeAdapter(list[tuple[str, str]])
_OPTIONAL_STRING_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)
_TARGET_KEY_PARTS = 4
_CONSOLIDATION_POLICY_VERSION = "memory-consolidation.v1"
_REFRESH_KINDS = ("team", "core", "daily", "user")
_MAX_REFRESH_TARGETS = 128


@dataclass(frozen=True, slots=True)
class _MemoryHead:
    document_id: str
    revision_id: str


@dataclass(frozen=True, slots=True)
class MemoryConsolidationProcessor:
    repository: SqliteKnowledgeRepository
    actor: ActorContext
    publisher: ChangePublisher

    def schedule(
        self,
        *,
        root_event_id: str,
        due_at: datetime,
        priority: JobPriority = JobPriority.ROUTINE,
        policy_version: str = _CONSOLIDATION_POLICY_VERSION,
    ) -> KnowledgeJob | None:
        heads = self._memory_heads()
        if not heads:
            return None
        input_digest = _input_revision_set_digest(
            workspace_id=self.actor.workspace_id,
            heads=heads,
            policy_version=policy_version,
        )
        unique_key = _consolidation_key(
            workspace_id=self.actor.workspace_id,
            input_digest=input_digest,
            policy_version=policy_version,
        )
        job = KnowledgeJob(
            schema="knowledge.job.v1",
            job_id=stable_id("job", unique_key),
            workspace_id=self.actor.workspace_id,
            scope=self.actor.conversation_scope,
            submitter_actor=self.actor
            if self.actor.conversation_scope.kind is ScopeKind.CHANNEL
            else None,
            kind=JobKind.MEMORY_CONSOLIDATE,
            state=JobState.QUEUED,
            priority=priority,
            root_event_id=root_event_id,
            policy_version=policy_version,
            due_at=due_at,
            created_at=due_at,
            reason_code="memory_consolidation",
        )
        return self._put_job(job, unique_key)

    def process(self, lease: JobLease, cancellation: Event) -> JobProcessResult:
        if cancellation.is_set():
            return _result(JobState.CANCELLED, "memory_refresh_cancelled")
        if lease.job.workspace_id != self.actor.workspace_id:
            return _result(JobState.FAILED, "memory_refresh_workspace_mismatch")
        if lease.job.scope not in memory_maintenance_scopes(self.actor):
            return _result(JobState.FAILED, "memory_refresh_scope_mismatch")
        scheduled = self.repository.scheduled_job_request(self.actor, lease.job.job_id)
        if scheduled is not None:
            return self._process_scheduled(lease, cancellation, scheduled.targets)
        match lease.job.kind:
            case JobKind.MEMORY_CONSOLIDATE:
                result = self._consolidate(lease, cancellation)
            case JobKind.MEMORY_SUMMARY_REFRESH:
                result = self._refresh_summary(lease, cancellation)
            case JobKind.MEMORY_VIEW_REFRESH:
                result = self._refresh_view(lease, cancellation)
            case _:
                result = _result(JobState.FAILED, "memory_refresh_kind_unsupported")
        return result

    def _process_scheduled(
        self,
        lease: JobLease,
        cancellation: Event,
        targets: tuple[str, ...],
    ) -> JobProcessResult:
        heads: list[_MemoryHead] = []
        for document_id in dict.fromkeys(targets):
            stored = self.repository.read_memory(self.actor, document_id)
            if (
                stored is None
                or stored.document.kind is MemoryKind.SOUL
                or stored.document.owned_scope not in memory_maintenance_scopes(self.actor)
                or any(entry.scope != stored.document.owned_scope for entry in stored.entries)
            ):
                return _result(JobState.FAILED, "memory_refresh_target_invalid")
            heads.append(_MemoryHead(document_id, stored.revision.revision_id))
        if lease.job.kind is JobKind.MEMORY_CONSOLIDATE:
            return self._consolidate(lease, cancellation, tuple(heads))
        for target in heads:
            match lease.job.kind:
                case JobKind.MEMORY_SUMMARY_REFRESH:
                    result = self._refresh_summary(lease, cancellation, target)
                case JobKind.MEMORY_VIEW_REFRESH:
                    result = self._refresh_view(lease, cancellation, target)
                case _:
                    return _result(JobState.FAILED, "memory_refresh_kind_unsupported")
            if result.state is not JobState.COMPLETED:
                return result
        return _result(JobState.COMPLETED, "memory_scheduled_refresh_completed")

    def _consolidate(
        self,
        lease: JobLease,
        cancellation: Event,
        heads: tuple[_MemoryHead, ...] | None = None,
    ) -> JobProcessResult:
        if heads is None:
            heads = self._memory_heads()
        if cancellation.is_set():
            return _result(JobState.CANCELLED, "memory_consolidation_cancelled")
        published = 0
        for head in heads:
            stored = self.repository.read_memory(self.actor, head.document_id, head.revision_id)
            if stored is None or stored.document.kind is MemoryKind.SOUL:
                continue
            entries = _consolidated_entries(stored.entries, lease.job.due_at)
            snapshot = (
                None
                if entries == stored.entries
                else self._publish(
                    lease,
                    stored,
                    entries,
                    purpose="consolidate",
                    reason="Supersede expired or duplicate canonical memory entries.",
                )
            )
            current = (
                head
                if snapshot is None
                else _MemoryHead(snapshot.document.document_id, snapshot.revision.revision_id)
            )
            if snapshot is not None:
                published += 1
            unique_key = _summary_key(current, lease.job.policy_version)
            job = _descendant_job(
                parent=lease.job,
                kind=JobKind.MEMORY_SUMMARY_REFRESH,
                unique_key=unique_key,
                reason_code="memory_summary_refresh",
                scope=stored.document.owned_scope,
            )
            _ = self._put_job(job, unique_key)
        return _result(JobState.COMPLETED, f"memory_consolidated:{published}")

    def _refresh_summary(
        self,
        lease: JobLease,
        cancellation: Event,
        target: _MemoryHead | None = None,
    ) -> JobProcessResult:
        if target is None:
            target = _target_from_key(
                lease.job.kind, self._unique_key(lease.job.job_id), lease.job.policy_version
            )
        if target is None:
            return _result(JobState.FAILED, "memory_summary_target_invalid")
        if cancellation.is_set():
            return _result(JobState.CANCELLED, "memory_summary_refresh_cancelled")
        if target not in self._memory_heads():
            return _result(JobState.COMPLETED, "memory_summary_input_superseded")
        stored = self.repository.read_memory(self.actor, target.document_id, target.revision_id)
        if stored is None or stored.document.kind is MemoryKind.SOUL:
            return _result(JobState.COMPLETED, "memory_summary_not_applicable")
        entries = self._refreshed_entries(stored.entries)
        if entries != stored.entries:
            snapshot = self._publish(
                lease,
                stored,
                entries,
                purpose="summary-refresh",
                reason="Refresh derived memory dependency state against current Wiki claims.",
            )
            if snapshot is None:
                return _result(JobState.FAILED, "memory_summary_publish_failed")
            target = _MemoryHead(snapshot.document.document_id, snapshot.revision.revision_id)
        unique_key = _view_key(target, lease.job.policy_version)
        job = _descendant_job(
            parent=lease.job,
            kind=JobKind.MEMORY_VIEW_REFRESH,
            unique_key=unique_key,
            reason_code="memory_view_refresh",
            scope=stored.document.owned_scope,
        )
        _ = self._put_job(job, unique_key)
        return _result(JobState.COMPLETED, "memory_summary_refresh_queued_view")

    def _refresh_view(
        self,
        lease: JobLease,
        cancellation: Event,
        target: _MemoryHead | None = None,
    ) -> JobProcessResult:
        if target is None:
            target = _target_from_key(
                lease.job.kind, self._unique_key(lease.job.job_id), lease.job.policy_version
            )
        if target is None:
            return _result(JobState.FAILED, "memory_view_target_invalid")
        if cancellation.is_set():
            return _result(JobState.CANCELLED, "memory_view_refresh_cancelled")
        result = MemoryViewDispatcher(self.repository, self.actor).dispatch_target(
            target.document_id, target.revision_id
        )
        return _view_result(result)

    def _publish(
        self,
        lease: JobLease,
        stored: StoredMemory,
        entries: tuple[MemoryEntry, ...],
        *,
        purpose: str,
        reason: str,
    ) -> MemorySnapshot | None:
        changed = next(
            (
                entry
                for entry, previous in zip(entries, stored.entries, strict=True)
                if entry != previous
            ),
            None,
        )
        if changed is None:
            return None
        operation_id = stable_id(
            "memory-maintenance",
            purpose,
            lease.job.job_id,
            stored.document.document_id,
            stored.revision.revision_id,
        )
        revision_id = stable_id(
            "memory-revision",
            operation_id,
            stored.revision.body_sha256,
        )
        revision = MemoryRevision(
            document_id=stored.document.document_id,
            revision_id=revision_id,
            previous_revision_id=stored.revision.revision_id,
            body_sha256=stored.revision.body_sha256,
            entry_ids=tuple(entry.entry_id for entry in entries),
            created_at=lease.job.due_at,
        )
        snapshot = MemorySnapshot(
            document=stored.document.model_copy(update={"head_revision_id": revision_id}),
            revision=revision,
            entries=entries,
            body=stored.body,
        )
        operation = MemoryOperation(
            operation_id=operation_id,
            kind=MemoryOperationKind.UPDATE,
            document_id=stored.document.document_id,
            entry_id=changed.entry_id,
            expected_revision_id=stored.revision.revision_id,
            reason=reason,
            evidence_refs=tuple(ref.evidence_id for ref in changed.source_refs),
        )
        _ = self.publisher.publish(
            actor=self.actor,
            group=ChangeGroup(operation_id=operation_id, memory_operations=(operation,)),
            pages=None,
            memories=(MemoryPublication(snapshot),),
            at=lease.job.due_at,
        )
        return snapshot

    def _refreshed_entries(self, entries: tuple[MemoryEntry, ...]) -> tuple[MemoryEntry, ...]:
        return tuple(
            self._refresh_entry(entry) if entry.origin is MemoryOrigin.WIKI_SUMMARY else entry
            for entry in entries
        )

    def _refresh_entry(self, entry: MemoryEntry) -> MemoryEntry:
        reference = entry.wiki_ref
        if reference is None:
            return entry.model_copy(update={"dependency_state": DependencyState.RESTRICTED})
        page = self.repository.read_page(self.actor, reference.page_id)
        if page is None or page.page.status is WikiPageStatus.RETRACTED:
            return entry.model_copy(update={"dependency_state": DependencyState.RESTRICTED})
        claim = next(
            (item for item in page.revision.claims if item.claim_id == reference.claim_id),
            None,
        )
        if claim is None or claim.status not in {ClaimStatus.ACTIVE, ClaimStatus.CONTESTED}:
            return entry.model_copy(update={"dependency_state": DependencyState.RESTRICTED})
        fingerprint = claim_semantic_fingerprint(claim)
        if fingerprint == reference.semantic_fingerprint:
            return entry.model_copy(update={"dependency_state": DependencyState.CURRENT})
        source_refs = tuple(
            ref.model_copy(
                update={
                    "revision_id": page.revision.revision_id,
                    "quote_sha256": None,
                    "scope": entry.scope,
                    "instruction_authority": InstructionAuthority.DATA,
                    "provenance": Provenance.AGENT_DERIVED,
                }
            )
            if ref.evidence_kind is EvidenceKind.CLAIM
            and ref.evidence_id == reference.claim_id
            and ref.revision_id == reference.revision_id
            else ref
            for ref in entry.source_refs
        )
        return entry.model_copy(
            update={
                "text": claim.statement,
                "dependency_state": DependencyState.CURRENT,
                "source_refs": source_refs,
                "wiki_ref": reference.model_copy(
                    update={
                        "revision_id": page.revision.revision_id,
                        "semantic_fingerprint": fingerprint,
                    }
                ),
            }
        )

    def _memory_heads(self) -> tuple[_MemoryHead, ...]:
        with self.repository.connection() as connection:
            rows = connection.execute(
                """
                SELECT document.document_id,head.revision_id
                FROM memory_documents AS document
                JOIN memory_heads AS head
                    ON head.workspace_id=document.workspace_id
                    AND head.document_id=document.document_id
                JOIN access_scopes AS scope ON scope.scope_key=document.scope_key
                WHERE document.workspace_id=? AND document.kind IN (?,?,?,?)
                    AND document.scope_key IN (SELECT value FROM json_each(?))
                    AND EXISTS (
                        SELECT 1 FROM memory_entries AS entry
                        WHERE entry.workspace_id=head.workspace_id
                            AND entry.document_id=head.document_id
                            AND entry.memory_revision_id=head.revision_id
                    )
                ORDER BY CASE document.kind
                    WHEN 'team' THEN 0 WHEN 'core' THEN 1 ELSE 2 END,
                    document.local_date,document.document_id
                LIMIT ?
                """,
                (
                    self.actor.workspace_id,
                    *_REFRESH_KINDS,
                    memory_maintenance_scope_keys(self.actor),
                    _MAX_REFRESH_TARGETS,
                ),
            ).fetchall()
        return tuple(
            _MemoryHead(str(row[0]), str(row[1])) for row in _HEAD_ROWS.validate_python(rows)
        )

    def _unique_key(self, job_id: str) -> str:
        with self.repository.connection() as connection:
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    "SELECT unique_key FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
            )
        return "" if row is None else str(row[0])

    def _put_job(self, job: KnowledgeJob, unique_key: str) -> KnowledgeJob:
        existing = self._existing_job(unique_key)
        if existing is not None:
            return existing
        try:
            self.repository.put_job(JobRegistration(job=job, unique_key=unique_key))
        except RepositoryConflictError:
            existing = self._existing_job(unique_key)
            if existing is None:
                raise
            return existing
        return job

    def _existing_job(self, unique_key: str) -> KnowledgeJob | None:
        with self.repository.connection() as connection:
            row = _OPTIONAL_STRING_ROW.validate_python(
                connection.execute(
                    """SELECT job_json FROM jobs WHERE workspace_id=? AND unique_key=?
                    ORDER BY job_id LIMIT 1""",
                    (self.actor.workspace_id, unique_key),
                ).fetchone()
            )
        return None if row is None else KnowledgeJob.model_validate_json(str(row[0]))


def _input_revision_set_digest(
    *,
    workspace_id: str,
    heads: tuple[_MemoryHead, ...],
    policy_version: str,
) -> str:
    return contract_sha256(
        {
            "workspace_id": workspace_id,
            "heads": [
                {"document_id": head.document_id, "revision_id": head.revision_id} for head in heads
            ],
            "policy_version": policy_version,
        }
    )


def _consolidation_key(*, workspace_id: str, input_digest: str, policy_version: str) -> str:
    return f"memory-consolidate:{_encode(workspace_id)}:{input_digest}:{_encode(policy_version)}"


def _summary_key(head: _MemoryHead, policy_version: str) -> str:
    return _target_key("memory-summary-refresh", head, policy_version)


def _view_key(head: _MemoryHead, policy_version: str) -> str:
    return _target_key("memory-view-refresh", head, policy_version)


def _target_key(prefix: str, head: _MemoryHead, policy_version: str) -> str:
    return ":".join(
        (prefix, _encode(head.document_id), _encode(head.revision_id), _encode(policy_version))
    )


def _target_from_key(kind: JobKind, unique_key: str, policy_version: str) -> _MemoryHead | None:
    prefix = {
        JobKind.MEMORY_SUMMARY_REFRESH: "memory-summary-refresh",
        JobKind.MEMORY_VIEW_REFRESH: "memory-view-refresh",
    }.get(kind)
    if prefix is None:
        return None
    parts = unique_key.split(":")
    if len(parts) != _TARGET_KEY_PARTS or parts[0] != prefix:
        return None
    document_id, revision_id, encoded_policy = (
        _decode(parts[1]),
        _decode(parts[2]),
        _decode(parts[3]),
    )
    if not document_id or not revision_id or encoded_policy != policy_version:
        return None
    return _MemoryHead(document_id, revision_id)


def _descendant_job(
    *,
    parent: KnowledgeJob,
    kind: JobKind,
    unique_key: str,
    reason_code: str,
    scope: AccessScope,
) -> KnowledgeJob:
    return KnowledgeJob(
        schema="knowledge.job.v1",
        job_id=stable_id("job", unique_key),
        workspace_id=parent.workspace_id,
        scope=scope,
        submitter_actor=parent.submitter_actor,
        kind=kind,
        state=JobState.QUEUED,
        priority=parent.priority,
        root_event_id=parent.root_event_id,
        policy_version=parent.policy_version,
        due_at=parent.due_at,
        created_at=parent.created_at,
        reason_code=reason_code,
    )


def _encode(value: str) -> str:
    return quote(value, safe="._-")


def _decode(value: str) -> str:
    return unquote(value)


def _result(state: JobState, code: str) -> JobProcessResult:
    return JobProcessResult(state=state, payload=code.encode())


def _consolidated_entries(
    entries: tuple[MemoryEntry, ...], at: datetime
) -> tuple[MemoryEntry, ...]:
    canonical: set[str] = set()
    result: list[MemoryEntry] = []
    for entry in entries:
        if entry.status is not MemoryStatus.ACTIVE:
            result.append(entry)
            continue
        if entry.expires_at is not None and entry.expires_at <= at:
            result.append(entry.model_copy(update={"status": MemoryStatus.SUPERSEDED}))
            continue
        if entry.dependency_state is not DependencyState.CURRENT:
            result.append(entry)
            continue
        key = contract_sha256(
            {
                "kind": entry.kind.value,
                "origin": entry.origin.value,
                "text": entry.text,
                "usage_role": entry.usage_role.value,
                "source_refs": [ref.model_dump(mode="json") for ref in entry.source_refs],
                "wiki_ref": None
                if entry.wiki_ref is None
                else entry.wiki_ref.model_dump(mode="json"),
                "applicability": None
                if entry.applicability is None
                else entry.applicability.model_dump(mode="json"),
            }
        )
        if key in canonical:
            result.append(entry.model_copy(update={"status": MemoryStatus.SUPERSEDED}))
            continue
        canonical.add(key)
        result.append(entry)
    return tuple(result)


def _view_result(result: MemoryViewDispatchResult) -> JobProcessResult:
    return _result(JobState.COMPLETED if result.completed else JobState.FAILED, result.code)


__all__ = ["MemoryConsolidationProcessor", "MemoryViewDispatcher"]

from __future__ import annotations

# ruff: noqa: EM101
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Final, assert_never
from unicodedata import normalize

from ads_booster.knowledge.contract_types import (
    ConversationEventKind,
    ConversationRole,
    DependencyState,
    EvidenceKind,
    MemoryEntryKind,
    MemoryKind,
    MemoryOrigin,
    MemoryStatus,
    ScopeKind,
    SourceKind,
    UsageRole,
)
from ads_booster.knowledge.curation_context import authenticated_user_event
from ads_booster.knowledge.curation_contracts import CurationMemoryDestination
from ads_booster.knowledge.curation_memory_admission import admit_memory_source
from ads_booster.knowledge.curation_memory_payload import (
    existing_subject_entry,
    memory_payload,
    memory_target,
)
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.grant_policy import authorize_write
from ads_booster.knowledge.ingestion_build import stable_id
from ads_booster.knowledge.memory_contracts import MemoryEntry
from ads_booster.knowledge.repository_identity import scope_key
from ads_booster.knowledge.repository_tool_state import RepositoryToolState, ToolStateError
from ads_booster.knowledge.repository_types import RepositoryConflictError
from ads_booster.knowledge.tool_contracts import (
    ApplyData,
    ToolResult,
    ToolResultStatus,
)
from ads_booster.knowledge.tool_support import KnowledgeToolError, error_result, success

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.knowledge.curation_contracts import CurationMemoryIntent, CurationRequest
    from ads_booster.knowledge.curation_runtime import CurationToolHost
    from ads_booster.knowledge.evidence_contracts import EvidenceRef
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.tool_contracts import TrustedInvocationContext


_MAX_EVIDENCE: Final = 128
_MAX_TEXT: Final = 20_000
_MAX_SUBJECT: Final = 500


@dataclass(frozen=True, slots=True)
class CurationMemoryWriter:
    """Compile semantic facts into the canonical publisher's CAS-bound memory payload."""

    repository: SqliteKnowledgeRepository
    host: CurationToolHost

    def write(
        self,
        request: CurationRequest,
        intent: CurationMemoryIntent,
        context: TrustedInvocationContext,
    ) -> ToolResult:
        subject = " ".join(normalize("NFKC", intent.subject_key).split())
        operation_id = stable_id(
            "curation.memory",
            context.actor.workspace_id,
            request.job_id,
            request.event_id,
            str(request.event_revision),
            subject.casefold(),
            intent.text,
            *sorted(intent.evidence_ids),
            *(
                (intent.destination.value,)
                if intent.destination is CurationMemoryDestination.USER
                else ()
            ),
        )
        if not subject or len(subject) > _MAX_SUBJECT:
            return error_result(operation_id, "curation_memory_subject_invalid")
        try:
            if context.actor.conversation_scope.kind not in (
                ScopeKind.WORKSPACE,
                ScopeKind.CHANNEL,
            ):
                return error_result(operation_id, "curation_memory_requires_shared_scope")
            document_kind, target_scope = memory_target(intent.destination, context.actor)
            _ = authorize_write(
                actor=context.actor,
                target_scope=target_scope,
                at=context.invoked_at,
            )
            document_id = self.repository.find_memory_document_id(
                context.actor,
                document_kind,
                None,
                None,
            ) or (
                f"memory.{document_kind.value}.{scope_key(target_scope)}"
                if target_scope.kind is not ScopeKind.WORKSPACE
                else "memory.core"
            )
            stored = self.repository.read_memory(context.actor, document_id)
            refs, latest = self._evidence(request, intent, context)
            receipt = self.repository.operation_receipt(context.actor, operation_id)
            if receipt is not None:
                admit_memory_source(
                    self.repository, request, context, operation_id, destination=intent.destination
                )
                return success(operation_id, ToolResultStatus.REPLAYED, ApplyData(receipt=receipt))
            entry_id = stable_id(
                "memory.fact",
                target_scope.model_dump_json(),
                subject.casefold(),
            )
            previous = existing_subject_entry(stored, subject)
            if previous is not None:
                entry_id = previous.entry_id
            refs = self._preserve_evidence(previous, refs, latest, context)
            text = f"{subject}: {intent.text}"
            if len(refs) > _MAX_EVIDENCE or len(text) > _MAX_TEXT:
                return error_result(operation_id, "curation_memory_capacity_exceeded")
            entry = MemoryEntry(
                entry_id=entry_id,
                document_id=document_id,
                document_kind=document_kind,
                text=text,
                kind=MemoryEntryKind.FACT,
                status=MemoryStatus.ACTIVE,
                dependency_state=DependencyState.CURRENT,
                origin=MemoryOrigin.DIRECT,
                usage_role=UsageRole.REFERENCE,
                scope=target_scope if previous is None else previous.scope,
                source_refs=refs,
                applicability=AppliesTo(subject_key=subject)
                if previous is None
                else previous.applicability,
                expires_at=None if previous is None else previous.expires_at,
                review_after=None if previous is None else previous.review_after,
                admission_reason="Factual conversation memory with canonical user evidence.",
            )
            payload = memory_payload(self.repository, stored, entry, operation_id, context)
            result = self.host.execute(
                "memory_apply",
                payload.model_dump(mode="json", by_alias=True),
                context,
            )
            if result.status in (ToolResultStatus.APPLIED, ToolResultStatus.REPLAYED):
                admit_memory_source(
                    self.repository, request, context, operation_id, destination=intent.destination
                )
        except (
            RepositoryConflictError,
            KnowledgeToolError,
            KnowledgePolicyError,
            ToolStateError,
        ) as error:
            return _error_result(operation_id, error)
        return result

    def _preserve_evidence(
        self,
        previous: MemoryEntry | None,
        refs: tuple[EvidenceRef, ...],
        latest: datetime,
        context: TrustedInvocationContext,
    ) -> tuple[EvidenceRef, ...]:
        if previous is None:
            return refs
        selected_ids = {ref.evidence_id for ref in refs}
        for ref in previous.source_refs:
            if ref.evidence_id in selected_ids:
                continue
            try:
                event = RepositoryToolState(self.repository).read_canonical_event(
                    context.actor,
                    ref.evidence_id,
                )
            except ToolStateError as error:
                if error.code == "authenticated_event_not_found":
                    raise KnowledgeToolError("curation_memory_previous_evidence_stale") from error
                raise
            if (
                ref.evidence_kind is not EvidenceKind.CONVERSATION_EVENT
                or str(event.revision) != ref.revision_id
                or sha256(event.text.encode()).hexdigest() != ref.quote_sha256
                or event.scope != ref.scope
                or event.role is not ConversationRole.USER
                or event.quoted_spans
                or event.event_kind is ConversationEventKind.MESSAGE_DELETED
                or (
                    previous.document_kind is MemoryKind.USER
                    and (
                        event.speaker_ref != context.actor.actor_id
                        or event.scope != context.actor.conversation_scope
                    )
                )
            ):
                raise KnowledgeToolError("curation_memory_previous_evidence_stale")
            if (event.edited_at or event.created_at) > latest:
                raise RepositoryConflictError(
                    "curation_memory_newer_evidence_exists", previous.entry_id
                )
        return (
            tuple(ref for ref in previous.source_refs if ref.evidence_id not in selected_ids) + refs
        )

    def _evidence(
        self,
        request: CurationRequest,
        intent: CurationMemoryIntent,
        context: TrustedInvocationContext,
    ) -> tuple[tuple[EvidenceRef, ...], datetime]:
        """Resolve only host-provided, current public evidence from the same conversation."""
        current = request.authenticated_user_event
        if current is None or (
            current.evidence_ref.revision_id != str(request.event_revision)
            or current.evidence_ref.evidence_id not in intent.evidence_ids
            or request.job_id != context.job_id
        ):
            raise KnowledgeToolError("curation_memory_current_evidence_required")
        available = {
            item.evidence.evidence_ref.evidence_id: item for item in request.conversation_evidence
        }
        if len(set(intent.evidence_ids)) != len(intent.evidence_ids):
            raise KnowledgeToolError("curation_memory_duplicate_evidence")
        current_event = RepositoryToolState(self.repository).read_canonical_event(
            context.actor,
            current.evidence_ref.evidence_id,
        )
        latest = current_event.edited_at or current_event.created_at
        refs: list[EvidenceRef] = []
        for evidence_id in intent.evidence_ids:
            selected = available.get(evidence_id)
            if selected is None:
                raise KnowledgeToolError("curation_memory_evidence_not_provided")
            event = RepositoryToolState(self.repository).read_canonical_event(
                context.actor, evidence_id
            )
            if (
                event.scope.kind not in (ScopeKind.WORKSPACE, ScopeKind.CHANNEL)
                or (
                    intent.destination is CurationMemoryDestination.USER
                    and event.speaker_ref != context.actor.actor_id
                )
                or event.scope != context.actor.conversation_scope
                or event.conversation_id != current_event.conversation_id
                or (event.edited_at or event.created_at) > latest
                or selected.text != event.text
                or selected.occurred_at != (event.edited_at or event.created_at)
            ):
                raise KnowledgeToolError("curation_memory_evidence_binding_mismatch")
            source = self.repository.source_by_identity(
                context.actor,
                SourceKind.MESSAGE,
                f"message:{event.conversation_id}:{evidence_id}",
            )
            stored = (
                None
                if source is None
                else self.repository.read_source(context.actor, source.source_id)
            )
            if stored is None:
                raise KnowledgeToolError("curation_memory_source_unavailable")
            if evidence_id == current.evidence_ref.evidence_id and not any(
                capability.source_id == stored.source.source_id
                and capability.revision_id == stored.source.revision_id
                and capability.actor_ref == context.actor.actor_id
                and capability.workspace_id == context.actor.workspace_id
                and (capability.expires_at is None or context.invoked_at < capability.expires_at)
                for capability in context.source_capabilities
            ):
                raise KnowledgeToolError("curation_memory_source_capability_missing")
            try:
                authenticated = authenticated_user_event(self.repository, context.actor, stored)
            except ValueError as error:
                raise KnowledgeToolError("curation_memory_source_revision_stale") from error
            if authenticated != selected.evidence or (
                evidence_id == current.evidence_ref.evidence_id and authenticated != current
            ):
                raise KnowledgeToolError("curation_memory_evidence_binding_mismatch")
            refs.append(selected.evidence.evidence_ref)
        return tuple(refs), latest


def _error_result(
    operation_id: str,
    error: RepositoryConflictError | KnowledgeToolError | KnowledgePolicyError | ToolStateError,
) -> ToolResult:
    match error:
        case RepositoryConflictError():
            return error_result(operation_id, error.code, status=ToolResultStatus.CONFLICT)
        case KnowledgeToolError() | KnowledgePolicyError() | ToolStateError():
            return error_result(operation_id, error.code)
    assert_never(error)

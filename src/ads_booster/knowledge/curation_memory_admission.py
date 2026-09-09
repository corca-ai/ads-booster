from __future__ import annotations

# ruff: noqa: EM101
from typing import TYPE_CHECKING

from ads_booster.knowledge.contract_types import SourceDisposition, SourceKind
from ads_booster.knowledge.curation_contracts import (
    CurationMemoryDestination,
    SourceDispositionIntent,
)
from ads_booster.knowledge.curation_disposition import (
    DispositionApplyRequest,
    RepositorySourceDisposition,
    SourceDispositionError,
)
from ads_booster.knowledge.ingestion_build import stable_id
from ads_booster.knowledge.repository_tool_state import RepositoryToolState
from ads_booster.knowledge.repository_types import RepositoryConflictError
from ads_booster.knowledge.tool_support import KnowledgeToolError

if TYPE_CHECKING:
    from ads_booster.knowledge.curation_contracts import CurationRequest
    from ads_booster.knowledge.repository import SqliteKnowledgeRepository
    from ads_booster.knowledge.tool_contracts import TrustedInvocationContext


def admit_memory_source(
    repository: SqliteKnowledgeRepository,
    request: CurationRequest,
    context: TrustedInvocationContext,
    operation_id: str,
    *,
    destination: CurationMemoryDestination = CurationMemoryDestination.CHANNEL,
) -> None:
    """Keep personal evidence out of shared search after its memory publication succeeds."""
    current = request.authenticated_user_event
    if current is None:
        raise KnowledgeToolError("curation_memory_current_evidence_required")
    event = RepositoryToolState(repository).read_canonical_event(
        context.actor,
        current.evidence_ref.evidence_id,
    )
    source = repository.source_by_identity(
        context.actor,
        SourceKind.MESSAGE,
        f"message:{event.conversation_id}:{event.message_id}",
    )
    if source is None or source.revision != request.event_revision:
        raise KnowledgeToolError("curation_memory_source_revision_stale")
    disposition = (
        SourceDisposition.USE_ONLY
        if destination is CurationMemoryDestination.USER
        else SourceDisposition.ADMIT
    )
    if source.disposition is disposition:
        return
    if disposition is SourceDisposition.ADMIT and source.disposition in (
        SourceDisposition.ADMIT,
        SourceDisposition.UPDATE,
        SourceDisposition.REFERENCE,
    ):
        return
    try:
        _ = RepositorySourceDisposition(repository).apply(
            DispositionApplyRequest(
                intent=SourceDispositionIntent(
                    source_id=source.source_id,
                    revision_id=source.revision_id,
                    expected_admission_revision=source.admission_revision,
                    disposition=disposition,
                    reason="Canonical factual memory published from this evidence.",
                ),
                trusted_context=context,
                operation_id=stable_id("curation.memory.admit", operation_id, disposition.value),
            )
        )
    except RepositoryConflictError as error:
        if (
            destination is CurationMemoryDestination.CHANNEL
            and error.code == "personal_source_not_shareable"
        ):
            return
        raise
    except SourceDispositionError as error:
        raise KnowledgeToolError(error.code) from error

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.contracts import (
    AccessScope,
    ConversationEvent,
    ConversationEventKind,
    ConversationRole,
    IngestEnvelope,
    IngestReceipt,
    KnowledgeJob,
    MessageEventRef,
    ScopeKind,
)
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.maintenance_jobs import CanonicalJobProcessor
from ads_booster.knowledge.messages import MessageValidationError
from ads_booster.knowledge.repository import JobClaim, MembershipRole, SqliteKnowledgeRepository
from ads_booster.knowledge.repository_tool_state import RepositoryToolState
from ads_booster.knowledge.tool_contracts import KnowledgeQuestionInput, ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.batch_runtime_support import batch_fixture
from tests.knowledge.change_test_fixtures import NOW, actor

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


type CurationInput = tuple[
    SqliteKnowledgeRepository, CanonicalJobProcessor, KnowledgeJob, ConversationEvent, IngestReceipt
]


@pytest.fixture
def curation_input(tmp_path: Path) -> Iterator[CurationInput]:
    yield from curation_fixture(
        tmp_path, "한국 팀의 확정 가격은 31,000원입니다.\n두 번째 문장도 온전히 보존합니다."
    )


def curation_fixture(tmp_path: Path, text: str) -> Iterator[CurationInput]:
    dependencies = batch_fixture(tmp_path / "dependencies")
    repository = SqliteKnowledgeRepository(tmp_path / "store")
    scoped_actor = actor()
    repository.register_actor(scoped_actor, MembershipRole.ADMIN)
    event = ConversationEvent(
        conversation_id="conversation.input",
        message_id="message.input",
        revision=1,
        sequence=1,
        role=ConversationRole.USER,
        speaker_ref=scoped_actor.actor_id,
        created_at=NOW,
        event_kind=ConversationEventKind.MESSAGE_FINALIZED,
        scope=scoped_actor.conversation_scope,
        text=text,
    )
    delivery = KnowledgeIngestion(repository).ingest(
        scoped_actor, event, envelope(event, "delivery.input")
    )
    lease = repository.claim_job(JobClaim("worker.input", NOW, NOW + timedelta(minutes=1)))
    assert lease is not None
    processor = CanonicalJobProcessor(
        repository,
        scoped_actor,
        dependencies.runtime.jobs.curation,
        dependencies.runtime.jobs.memory,
    )
    try:
        yield repository, processor, lease.job, event, delivery.unit_receipts[0].receipt
    finally:
        dependencies.close()


def test_curation_receives_complete_extracted_korean_text(curation_input: CurationInput) -> None:
    # Given
    _, processor, job, event, receipt = curation_input
    # When
    work = processor.build_curation_work(job)
    # Then
    assert work.request.objective == event.text
    assert work.request.excerpts[0].text == event.text
    capability = work.trusted_context.source_capabilities[0]
    assert capability.source_id == receipt.source_id
    assert capability.revision_id == receipt.source_revision_id
    assert capability.segment_ids == tuple(item.segment_id for item in work.request.excerpts)


def test_curation_rejects_job_for_superseded_source_head(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, _, _ = curation_input

    updated = curation_input[3].model_copy(
        update={
            "revision": 2,
            "event_kind": ConversationEventKind.MESSAGE_EDITED,
            "edited_at": NOW + timedelta(seconds=1),
            "text": "수정된 가격",
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        processor.actor, updated, envelope(updated, "delivery.edit")
    )
    # When / Then
    with pytest.raises(ValueError, match="curation_source_unavailable"):
        _ = processor.build_curation_work(job)


def envelope(event: ConversationEvent, delivery_id: str) -> IngestEnvelope:
    return IngestEnvelope(
        schema="knowledge.ingest-envelope.v1",
        delivery_id=delivery_id,
        event_kind=event.event_kind,
        request_text=event.text,
        message_event=MessageEventRef(
            conversation_ref=event.conversation_id,
            message_ref=event.message_id,
            revision=event.revision,
        ),
        timestamp=event.edited_at or event.created_at,
    )


def test_curation_cannot_read_source_from_another_workspace(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, _, _ = curation_input
    scope = AccessScope(kind=ScopeKind.WORKSPACE, workspace_id="workspace.other")
    outsider = processor.actor.model_copy(
        update={
            "workspace_id": scope.workspace_id,
            "conversation_scope": scope,
            "grants": tuple(
                grant.model_copy(update={"workspace_id": scope.workspace_id, "scope": scope})
                for grant in processor.actor.grants
            ),
        }
    )
    repository.register_actor(outsider, MembershipRole.ADMIN)
    # When / Then
    with pytest.raises(ValueError, match="curation_source_unavailable"):
        _ = processor.build_curation_work(job, outsider)


def test_workspace_maintenance_preserves_message_author(curation_input: CurationInput) -> None:
    repository, processor, job, event, _ = curation_input
    maintenance = processor.actor.model_copy(
        update={
            "actor_id": "actor.maintenance",
            "member_id": "member.maintenance",
            "session_id": "session.maintenance",
            "grants": tuple(
                grant.model_copy(update={"grant_id": "maintenance." + grant.grant_id})
                for grant in processor.actor.grants
            ),
        }
    )
    repository.register_actor(maintenance, MembershipRole.ADMIN)
    worker = replace(processor, actor=maintenance)
    work = worker.build_curation_work(job)
    assert work.trusted_context.actor == maintenance
    assert work.request.authenticated_user_event is not None
    assert work.request.authenticated_user_event.authority_ref.actor_ref == event.speaker_ref
    assert work.request.objective == event.text
    question = KnowledgeQuestionInput(
        schema="knowledge.tool.question.v1",
        question_id="question.maintenance",
        problem="Clarify this member's statement",
        evidence_ids=(event.message_id,),
        recommendation="Ask the author to clarify",
    )
    result = ToolHost(repository).execute(
        "knowledge_question", question.model_dump(mode="json", by_alias=True), work.trusted_context
    )
    assert result.status is ToolResultStatus.PENDING, result
    with pytest.raises(MessageValidationError, match="event_speaker_binding_mismatch"):
        _ = repository.canonical_event(maintenance, event.message_id)


def test_curation_evidence_read_does_not_grant_private_access(
    curation_input: CurationInput,
) -> None:
    repository, processor, _, event, _ = curation_input
    scope = AccessScope(
        kind=ScopeKind.MEMBER,
        workspace_id=processor.actor.workspace_id,
        member_id="member.private",
        session_id="session.private",
    )
    private_actor = processor.actor.model_copy(
        update={
            "actor_id": "actor.private",
            "conversation_scope": scope,
            "member_id": scope.member_id,
            "session_id": scope.session_id,
            "grants": tuple(
                grant.model_copy(update={"grant_id": "private." + grant.grant_id, "scope": scope})
                for grant in processor.actor.grants
            ),
        }
    )
    repository.register_actor(private_actor, MembershipRole.EDITOR)
    private_event = event.model_copy(
        update={
            "scope": scope,
            "speaker_ref": private_actor.actor_id,
            "message_id": "message.private",
            "conversation_id": "conversation.private",
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        private_actor, private_event, envelope(private_event, "delivery.private")
    )
    with pytest.raises(KnowledgePolicyError):
        _ = RepositoryToolState(repository).read_canonical_event(
            processor.actor, private_event.message_id
        )

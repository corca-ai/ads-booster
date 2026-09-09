from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, override

import pytest

from ads_booster.knowledge.contracts import AccessScope, MemoryKind, ScopeKind, SourceDisposition
from ads_booster.knowledge.curation import CurationRunner
from ads_booster.knowledge.curation_contracts import (
    CurationDecision,
    CurationDecisionAction,
    CurationMemoryDestination,
    CurationMemoryIntent,
    CurationRunStatus,
    SourceDispositionIntent,
)
from ads_booster.knowledge.curation_disposition import RepositorySourceDisposition
from ads_booster.knowledge.curation_memory import CurationMemoryWriter
from ads_booster.knowledge.curation_runtime import CurationDependencies
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.operation_enums import CurationTarget
from ads_booster.knowledge.repository import JobClaim, MembershipRole
from ads_booster.knowledge.tool_contracts import (
    KnowledgeSearchData,
    KnowledgeSearchInput,
    ToolResultStatus,
)
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.test_curation_inputs import curation_input as curation_input  # noqa: PLC0414
from tests.knowledge.test_curation_inputs import envelope
from tests.knowledge.test_curation_remember import MemoryProvider

if TYPE_CHECKING:
    from ads_booster.knowledge.batch_curation import CurationBatchWork
    from ads_booster.knowledge.curation_contracts import CurationObservation, CurationRequest
    from tests.knowledge.test_curation_inputs import CurationInput


def member_work(fixture: CurationInput, member: str, thread: str, text: str) -> CurationBatchWork:
    repository, processor, _, original, _ = fixture
    channel = AccessScope(
        kind=ScopeKind.CHANNEL,
        workspace_id=processor.actor.workspace_id,
        channel_id="C.preferences",
    )
    personal = AccessScope(
        kind=ScopeKind.CHANNEL_MEMBER,
        workspace_id=channel.workspace_id,
        channel_id=channel.channel_id,
        member_id=member,
    )
    scoped = processor.actor.model_copy(
        update={
            "actor_id": f"actor.{member}",
            "member_id": member,
            "session_id": f"{member}.{thread}",
            "conversation_scope": channel,
            "grants": tuple(
                grant.model_copy(
                    update={
                        "grant_id": f"{member}.{scope.kind.value}.{grant.capability.value}",
                        "scope": scope,
                    }
                )
                for scope in (channel, personal)
                for grant in processor.actor.grants
            ),
        }
    )
    repository.register_actor(scoped, MembershipRole.ADMIN)
    event = original.model_copy(
        update={
            "scope": channel,
            "speaker_ref": scoped.actor_id,
            "message_id": f"message.{member}.{thread}",
            "conversation_id": thread,
            "text": text,
            "created_at": datetime.now(UTC),
        }
    )
    ingested = KnowledgeIngestion(repository).ingest(
        scoped, event, envelope(event, f"delivery.{member}.{thread}")
    )
    now = datetime.now(UTC)
    while True:
        lease = repository.claim_job(JobClaim("worker.personal", now, now + timedelta(minutes=1)))
        assert lease is not None
        if lease.job.job_id == ingested.unit_receipts[0].receipt.curation_job_id:
            return processor.build_curation_work(lease.job, scoped)


def remember_user(fixture: CurationInput, work: CurationBatchWork, text: str) -> str:
    event = work.request.authenticated_user_event
    assert event is not None
    result = CurationMemoryWriter(fixture[0], ToolHost(fixture[0])).write(
        work.request,
        CurationMemoryIntent(
            destination=CurationMemoryDestination.USER,
            subject_key="응답 형식",
            text=text,
            evidence_ids=(event.evidence_ref.evidence_id,),
        ),
        work.trusted_context,
    )
    assert result.status is ToolResultStatus.APPLIED, result
    document_id = fixture[0].find_memory_document_id(
        work.trusted_context.actor, MemoryKind.USER, None, None
    )
    assert document_id is not None
    return document_id


def test_user_preference_is_personal_and_source_is_not_shared_search(
    curation_input: CurationInput,
) -> None:
    # Given
    repository = curation_input[0]
    alice = member_work(
        curation_input,
        "alice",
        "first",
        "나는 답변을 orchid-tone-a7 형식의 짧은 한국어로 받고 싶어.",
    )
    # When
    document_id = remember_user(
        curation_input, alice, "짧은 한국어 orchid-tone-a7 형식 답변을 선호한다."
    )
    # Then
    stored = repository.read_memory(alice.trusted_context.actor, document_id)
    assert stored is not None
    assert stored.document.kind is MemoryKind.USER
    assert stored.document.owned_scope.kind is ScopeKind.CHANNEL_MEMBER
    assert stored.document.owned_scope.member_id == "alice"
    assert (
        repository.find_memory_document_id(alice.trusted_context.actor, MemoryKind.CORE, None, None)
        is None
    )
    source = repository.read_source(
        alice.trusted_context.actor, alice.request.excerpts[0].source_id
    )
    assert source is not None
    assert source.source.disposition is SourceDisposition.USE_ONLY
    bob = member_work(curation_input, "bob", "second", "별도 업무를 시작하자.")
    result = ToolHost(repository).execute(
        "knowledge_search",
        KnowledgeSearchInput(
            schema="knowledge.tool.search.v1",
            query="orchid-tone-a7",
        ).model_dump(mode="json", by_alias=True),
        bob.trusted_context,
    )
    assert isinstance(result.data, KnowledgeSearchData)
    assert result.data.hits == ()


def test_user_correction_and_known_memory_do_not_change_peer_preferences(
    curation_input: CurationInput,
) -> None:
    # Given
    repository = curation_input[0]
    alice = member_work(curation_input, "alice", "first", "나는 짧은 한국어 답변을 선호해.")
    alice_id = remember_user(curation_input, alice, "짧은 한국어 답변을 선호한다.")
    bob = member_work(curation_input, "bob", "second", "나는 상세한 영어 답변을 선호해.")
    bob_id = remember_user(curation_input, bob, "상세한 영어 답변을 선호한다.")
    previous_bob = repository.read_memory(bob.trusted_context.actor, bob_id)
    correction = member_work(
        curation_input, "alice", "third", "앞으로 한국어로 충분한 근거를 설명해줘."
    )
    assert len(correction.request.known_memory) == 1
    assert correction.request.known_memory[0].document_id == alice_id
    # When
    corrected_id = remember_user(
        curation_input, correction, "한국어로 충분한 근거가 있는 설명을 선호한다."
    )
    # Then
    assert corrected_id == alice_id
    saved = repository.read_memory(correction.trusted_context.actor, alice_id)
    assert saved is not None
    assert len(saved.entries) == 1
    assert "충분한 근거" in saved.entries[0].text
    assert "짧은" not in saved.entries[0].text
    assert repository.read_memory(bob.trusted_context.actor, bob_id) == previous_bob
    assert bob_id != alice_id


def test_peer_authored_evidence_cannot_define_user_preference(
    curation_input: CurationInput,
) -> None:
    # Given
    bob = member_work(curation_input, "bob", "shared", "나는 상세한 영어 답변을 선호한다.")
    alice = member_work(curation_input, "alice", "shared", "나는 짧은 한국어 답변을 선호한다.")
    assert bob.request.authenticated_user_event is not None
    assert alice.request.authenticated_user_event is not None
    assert len(alice.request.conversation_evidence) == 2
    # When
    result = CurationMemoryWriter(curation_input[0], ToolHost(curation_input[0])).write(
        alice.request,
        CurationMemoryIntent(
            destination=CurationMemoryDestination.USER,
            subject_key="응답 형식",
            text="상세한 영어 답변",
            evidence_ids=(
                alice.request.authenticated_user_event.evidence_ref.evidence_id,
                bob.request.authenticated_user_event.evidence_ref.evidence_id,
            ),
        ),
        alice.trusted_context,
    )
    # Then
    assert result.status is ToolResultStatus.REJECTED
    assert result.error_code == "curation_memory_evidence_binding_mismatch"
    assert (
        curation_input[0].find_memory_document_id(
            alice.trusted_context.actor, MemoryKind.USER, None, None
        )
        is None
    )


def test_user_destination_is_not_available_to_workspace_actor(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    work = processor.build_curation_work(job)
    # When
    result = CurationMemoryWriter(repository, ToolHost(repository)).write(
        work.request,
        CurationMemoryIntent(
            destination=CurationMemoryDestination.USER,
            subject_key="응답 형식",
            text="짧은 답변",
            evidence_ids=(event.message_id,),
        ),
        work.trusted_context,
    )
    # Then
    assert result.status is ToolResultStatus.REJECTED
    assert result.error_code == "curation_user_memory_requires_channel"


@pytest.mark.parametrize("user_first", [False, True])
def test_mixed_core_and_user_facts_do_not_share_raw_preference_source(
    curation_input: CurationInput,
    user_first: bool,
) -> None:
    # Given
    repository = curation_input[0]
    alice = member_work(
        curation_input,
        "alice",
        "mixed",
        "공동 프로젝트 예산은 41000이다. 나는 personal-orchid 짧은 답변을 선호한다.",
    )
    current = alice.request.authenticated_user_event
    assert current is not None
    writer = CurationMemoryWriter(repository, ToolHost(repository))
    core = CurationMemoryIntent(
        subject_key="프로젝트 예산", text="41000", evidence_ids=(current.evidence_ref.evidence_id,)
    )
    if user_first:
        _ = remember_user(curation_input, alice, "personal-orchid 짧은 답변을 선호한다.")
    assert (
        writer.write(alice.request, core, alice.trusted_context).status is ToolResultStatus.APPLIED
    )
    if not user_first:
        _ = remember_user(curation_input, alice, "personal-orchid 짧은 답변을 선호한다.")
    # When
    replay = writer.write(alice.request, core, alice.trusted_context)
    # Then
    assert replay.status is ToolResultStatus.REPLAYED
    source = repository.read_source(
        alice.trusted_context.actor, alice.request.excerpts[0].source_id
    )
    assert source is not None
    assert source.source.disposition is SourceDisposition.USE_ONLY
    bob = member_work(curation_input, "bob", "separate", "별도 업무")
    host = ToolHost(repository)
    result = host.execute(
        "knowledge_search",
        KnowledgeSearchInput(
            schema="knowledge.tool.search.v1",
            query="personal-orchid",
        ).model_dump(mode="json", by_alias=True),
        bob.trusted_context,
    )
    assert isinstance(result.data, KnowledgeSearchData)
    assert result.data.hits == ()
    shared = host.execute(
        "knowledge_search",
        KnowledgeSearchInput(
            schema="knowledge.tool.search.v1",
            query="프로젝트 예산",
        ).model_dump(mode="json", by_alias=True),
        bob.trusted_context,
    )
    assert isinstance(shared.data, KnowledgeSearchData)
    assert any("41000" in hit.snippet for hit in shared.data.hits)


@dataclass(frozen=True, slots=True)
class UserPreferenceProvider(MemoryProvider):
    share_source_on_finish: bool = False

    @override
    def decide(
        self,
        request: CurationRequest,
        observations: tuple[CurationObservation, ...],
        *,
        timeout_seconds: float,
    ) -> CurationDecision:
        assert request.auto_memory_enabled
        assert timeout_seconds > 0
        current = request.authenticated_user_event
        assert current is not None
        if not observations:
            return CurationDecision(
                schema="knowledge.curation-decision.v1",
                action=CurationDecisionAction.REMEMBER,
                memory_intent=CurationMemoryIntent(
                    destination=CurationMemoryDestination.USER,
                    subject_key="응답 형식",
                    text="근거 있는 한국어 답변을 선호한다.",
                    evidence_ids=(current.evidence_ref.evidence_id,),
                ),
            )
        disposition = None
        if self.share_source_on_finish:
            disposition = SourceDispositionIntent(
                source_id=request.excerpts[0].source_id,
                revision_id=request.excerpts[0].revision_id,
                expected_admission_revision=0,
                disposition=SourceDisposition.REFERENCE,
                reason="Attempt ordinary finish admission after personal memory",
            )
        return CurationDecision(
            schema="knowledge.curation-decision.v1",
            action=CurationDecisionAction.FINISH,
            targets=(CurationTarget.USER,),
            finish_summary="Personal reference stored",
            disposition_intent=disposition,
        )


@pytest.mark.parametrize("attempt_shared_admission", [False, True])
def test_curation_user_finish_preserves_personal_source_boundary(
    curation_input: CurationInput,
    attempt_shared_admission: bool,
) -> None:
    # Given
    repository = curation_input[0]
    work = member_work(curation_input, "alice", "first", "나는 근거가 있는 한국어 답변을 선호해.")
    host = ToolHost(repository)
    runner = CurationRunner(
        CurationDependencies(
            provider=UserPreferenceProvider(share_source_on_finish=attempt_shared_admission),
            tool_host=host,
            dispositions=RepositorySourceDisposition(repository),
            memory=CurationMemoryWriter(repository, host),
        )
    )
    # When
    result = runner.run(work.request, work.trusted_context)
    # Then
    if attempt_shared_admission:
        assert result.status is CurationRunStatus.BUDGET_EXHAUSTED
        assert any(
            item.result.error_code == "personal_source_not_shareable"
            for item in result.observations
        )
    else:
        assert result.status is CurationRunStatus.FINISHED
        assert result.event_receipt.targets == (CurationTarget.USER,)
    source = repository.read_source(work.trusted_context.actor, work.request.excerpts[0].source_id)
    assert source is not None
    assert source.source.disposition is SourceDisposition.USE_ONLY

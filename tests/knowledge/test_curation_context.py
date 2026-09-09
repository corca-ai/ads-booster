from __future__ import annotations

from datetime import timedelta

from ads_booster.knowledge.contract_types import DependencyState, MemoryStatus, UsageRole
from ads_booster.knowledge.contracts import AccessScope, ConversationEventKind, ScopeKind
from ads_booster.knowledge.curation_context import select_reference_memory
from ads_booster.knowledge.governance_contracts import AppliesTo
from ads_booster.knowledge.ingestion import KnowledgeIngestion
from ads_booster.knowledge.repository import MembershipRole
from ads_booster.knowledge.source_contracts import QuotedSpan
from tests.knowledge.change_test_fixtures import PRIVATE_SCOPE, memory_entry
from tests.knowledge.test_curation_inputs import (
    CurationInput,
    envelope,
)
from tests.knowledge.test_curation_inputs import (
    curation_input as curation_input,  # noqa: PLC0414 - pytest fixture re-export
)


def test_curation_receives_prior_user_messages_with_equal_sequence(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    prior = event.model_copy(
        update={
            "message_id": "message.prior",
            "text": "Campaign Aurora budget is 370000",
            "created_at": event.created_at - timedelta(seconds=1),
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        processor.actor,
        prior,
        envelope(prior, "prior").model_copy(update={"timestamp": event.created_at}),
    )
    # When
    request = processor.build_curation_work(job).request
    # Then
    assert [item.text for item in request.conversation_evidence] == [prior.text, event.text]
    assert request.conversation_evidence[0].evidence.authority_ref.actor_ref == prior.speaker_ref


def test_curation_excludes_future_deleted_quoted_and_other_conversations(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    for name, updates in (
        ("future", {"created_at": event.created_at + timedelta(seconds=1)}),
        (
            "quoted",
            {"quoted_spans": (QuotedSpan(start=0, end=2, source_message_id="quoted.source"),)},
        ),
        ("other", {"conversation_id": "conversation.other"}),
        ("deleted", {"event_kind": ConversationEventKind.MESSAGE_DELETED, "text": ""}),
    ):
        candidate = event.model_copy(update={"message_id": f"message.{name}", **updates})
        _ = KnowledgeIngestion(repository).ingest(
            processor.actor, candidate, envelope(candidate, name)
        )
    # When
    request = processor.build_curation_work(job).request
    # Then
    assert [item.text for item in request.conversation_evidence] == [event.text]


def test_curation_excludes_future_revision_instead_of_resurrecting_old_text(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    prior = event.model_copy(
        update={
            "message_id": "message.edited",
            "text": "Old value",
            "created_at": event.created_at - timedelta(seconds=2),
        }
    )
    ingest = KnowledgeIngestion(repository)
    _ = ingest.ingest(
        processor.actor,
        prior,
        envelope(prior, "original").model_copy(
            update={"timestamp": event.created_at},
        ),
    )
    updated = prior.model_copy(
        update={
            "revision": 2,
            "sequence": 2,
            "text": "Future value",
            "event_kind": ConversationEventKind.MESSAGE_EDITED,
            "edited_at": event.created_at + timedelta(seconds=1),
        }
    )
    _ = ingest.ingest(processor.actor, updated, envelope(updated, "edited"))
    # When
    request = processor.build_curation_work(job).request
    # Then
    assert [item.text for item in request.conversation_evidence] == [event.text]


def test_curation_uses_edited_revision_before_current_message(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    ingest = KnowledgeIngestion(repository)
    prior = event.model_copy(
        update={
            "message_id": "message.edited",
            "text": "Old value",
            "created_at": event.created_at - timedelta(seconds=2),
        }
    )
    _ = ingest.ingest(
        processor.actor,
        prior,
        envelope(prior, "original").model_copy(
            update={"timestamp": event.created_at},
        ),
    )
    updated = prior.model_copy(
        update={
            "revision": 2,
            "sequence": 2,
            "text": "Corrected value",
            "event_kind": ConversationEventKind.MESSAGE_EDITED,
            "edited_at": event.created_at - timedelta(seconds=1),
        }
    )
    _ = ingest.ingest(
        processor.actor,
        updated,
        envelope(updated, "edited").model_copy(
            update={"timestamp": event.created_at},
        ),
    )
    # When
    request = processor.build_curation_work(job).request
    # Then
    assert [item.text for item in request.conversation_evidence] == [updated.text, event.text]
    assert request.conversation_evidence[0].evidence.evidence_ref.revision_id == "2"


def test_curation_bounds_recent_message_count(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    for index in range(15):
        prior = event.model_copy(
            update={
                "message_id": f"message.prior.{index}",
                "text": f"Fact {index}",
                "created_at": event.created_at - timedelta(seconds=15 - index),
            }
        )
        _ = KnowledgeIngestion(repository).ingest(
            processor.actor,
            prior,
            envelope(prior, f"prior.{index}").model_copy(
                update={"timestamp": event.created_at},
            ),
        )
    # When
    context = processor.build_curation_work(job).request.conversation_evidence
    # Then
    assert [item.text for item in context] == [*(f"Fact {i}" for i in range(4, 15)), event.text]


def test_curation_bounds_total_characters_without_truncating_evidence(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    for index in range(3):
        prior = event.model_copy(
            update={
                "message_id": f"message.large.{index}",
                "text": str(index) * 12000,
                "created_at": event.created_at - timedelta(seconds=3 - index),
            }
        )
        _ = KnowledgeIngestion(repository).ingest(
            processor.actor,
            prior,
            envelope(prior, f"large.{index}").model_copy(
                update={"timestamp": event.created_at},
            ),
        )
    # When
    context = processor.build_curation_work(job).request.conversation_evidence
    # Then
    assert [item.text for item in context] == ["2" * 12000, event.text]
    assert sum(len(item.text) for item in context) <= 24000


def test_curation_does_not_promote_private_context_with_same_conversation_id(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
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
                grant.model_copy(
                    update={
                        "grant_id": f"private.{grant.grant_id}",
                        "scope": scope,
                    }
                )
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
            "text": "Private secret",
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        private_actor,
        private_event,
        envelope(private_event, "private"),
    )
    # When
    context = processor.build_curation_work(job).request.conversation_evidence
    # Then
    assert [item.text for item in context] == [event.text]
    assert all(item.evidence.evidence_ref.scope == event.scope for item in context)


def test_curation_excludes_blocked_sources(curation_input: CurationInput) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    prior = event.model_copy(
        update={
            "message_id": "message.blocked",
            "text": "Blocked fact",
            "created_at": event.created_at - timedelta(seconds=1),
        }
    )
    delivery = KnowledgeIngestion(repository).ingest(
        processor.actor,
        prior,
        envelope(prior, "blocked").model_copy(
            update={"timestamp": event.created_at},
        ),
    )
    with repository.connection() as connection:
        _ = connection.execute(
            "UPDATE sources SET visibility='blocked' WHERE source_id=?",
            (delivery.unit_receipts[0].receipt.source_id,),
        )
    # When
    context = processor.build_curation_work(job).request.conversation_evidence
    # Then
    assert [item.text for item in context] == [event.text]


def test_curation_excludes_canonical_text_that_does_not_match_stored_source(
    curation_input: CurationInput,
) -> None:
    # Given
    repository, processor, job, event, _ = curation_input
    prior = event.model_copy(
        update={
            "message_id": "message.mismatch",
            "text": "Original fact",
            "created_at": event.created_at - timedelta(seconds=1),
        }
    )
    _ = KnowledgeIngestion(repository).ingest(
        processor.actor,
        prior,
        envelope(prior, "mismatch").model_copy(
            update={"timestamp": event.created_at},
        ),
    )
    changed = prior.model_copy(update={"text": "Unbacked canonical fact"})
    with repository.connection() as connection:
        _ = connection.execute(
            "UPDATE conversation_events SET event_json=? WHERE message_id=?",
            (changed.model_dump_json(), prior.message_id),
        )
    # When
    context = processor.build_curation_work(job).request.conversation_evidence
    # Then
    assert [item.text for item in context] == [event.text]


def test_known_memory_prefers_matching_subject(curation_input: CurationInput) -> None:
    # Given
    _, processor, job, event, _ = curation_input
    context = processor.build_curation_work(job).request.conversation_evidence
    matching = memory_entry(entry_id="entry.match").model_copy(
        update={
            "applicability": AppliesTo(subject_key=event.text[:4]),
        }
    )
    other = memory_entry(entry_id="entry.other").model_copy(
        update={
            "applicability": AppliesTo(subject_key="unrelated"),
        }
    )
    # When
    selected = select_reference_memory((other, matching), context)
    # Then
    assert selected == (matching,)


def test_known_memory_excludes_private_stale_and_constraint_entries() -> None:
    # Given
    active = memory_entry()
    excluded = (
        memory_entry(scope=PRIVATE_SCOPE),
        active.model_copy(update={"status": MemoryStatus.SUPERSEDED}),
        active.model_copy(update={"dependency_state": DependencyState.STALE}),
        active.model_copy(update={"usage_role": UsageRole.CONSTRAINT}),
    )
    # When
    selected = select_reference_memory((*excluded, active), ())
    # Then
    assert selected == (active,)


def test_known_memory_bounds_small_corpus_fallback() -> None:
    # Given
    entries = tuple(memory_entry(entry_id=f"entry.{index}") for index in range(17))
    # When
    selected = select_reference_memory(entries, ())
    # Then
    assert selected == ()


def test_known_memory_bounds_serialized_context_without_truncation() -> None:
    # Given
    entries = tuple(
        memory_entry(entry_id=f"entry.{index}").model_copy(
            update={
                "text": str(index) * 8000,
            }
        )
        for index in range(3)
    )
    # When
    selected = select_reference_memory(entries, ())
    # Then
    assert selected == (entries[0],)
    assert sum(len(entry.model_dump_json()) for entry in selected) <= 16000

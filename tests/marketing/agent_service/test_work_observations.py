"""Human effort remains scoped, corrigible and distinct from verified causal outcomes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.work_observation import WorkObservation
from ads_booster.learning.work_observations import WorkObservationStore

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def access(*, workspace: str = "team", work: str = "small", private: bool = False) -> MemoryAccess:
    return MemoryAccess(
        scope=MemoryScope(
            workspace_id=workspace,
            product_id="trace",
            work_id=work,
            member_id="member" if private else "",
            session_id="dm" if private else "",
        ),
        actor_id="member",
        private=private,
        can_review=True,
    )


def measurement(identity: MemoryAccess, **updates: object) -> WorkObservation:
    return WorkObservation.model_validate(
        {
            "observation_id": "first",
            "scope": identity.scope.model_dump(),
            "author_id": identity.actor_id,
            "source_ref": "slack:thread:message",
            "source_sha256": "a" * 64,
            "phase": "localization",
            "elapsed_minutes": 12,
            "revision_count": 1,
            "window_start": NOW,
            "window_end": NOW,
            "window_kind": "report_time",
            "recorded_at": NOW,
            "locale": "ja",
            **updates,
        }
    )


def test_replay_correction_and_restart_do_not_double_count(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    store = WorkObservationStore(database)
    identity = access()
    first = measurement(identity)
    assert store.record(first, identity) == store.record(first, identity)
    with pytest.raises(ValueError, match="idempotency_conflict"):
        _ = store.record(measurement(identity, elapsed_minutes=99), identity)
    corrected = measurement(
        identity, observation_id="second", supersedes="first", elapsed_minutes=9
    )
    _ = store.record(corrected, identity)
    summary = WorkObservationStore(database).summarize(identity)
    assert summary.observations == (corrected,)
    assert summary.elapsed_minutes == 9
    assert summary.revision_count == 1
    assert store.get("first", identity) == first
    with pytest.raises(ValueError, match="already_corrected"):
        _ = store.record(measurement(identity, observation_id="fork", supersedes="first"), identity)
    assert summary.evidence_status == "human_reported"


def test_scope_before_read_and_private_promotion_denied(tmp_path: Path) -> None:
    store = WorkObservationStore(tmp_path / "state.db")
    original = measurement(access(private=True))
    _ = store.record(original, access(private=True))
    for stranger in (
        access(),
        access(workspace="other", private=True),
        access(work="another", private=True),
    ):
        assert store.get("first", stranger) is None
        assert store.summarize(stranger).observations == ()
        with pytest.raises(ValueError, match="scope_or_author_denied"):
            _ = store.record(original, stranger)
    with pytest.raises(ValueError, match="correction_scope_denied"):
        _ = store.record(
            measurement(access(), observation_id="shared", supersedes="first"), access()
        )
    with pytest.raises(ValueError, match="requires_work"):
        _ = store.summarize(access(work=""))


def test_learning_snapshot_needs_review_and_correction_invalidates_currentness(
    tmp_path: Path,
) -> None:
    store = WorkObservationStore(tmp_path / "state.db")
    identity = access()
    _ = store.record(measurement(identity), identity)
    note = store.learning_candidate(
        identity,
        note_id="candidate",
        observation_ids=("first",),
        observation="일본어 가공에 12분이라고 보고",
        counterexample="실제 재캡처 비교 측정 없음",
        applicability="이 배경의 일본어 가공 실험",
        now=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    assert note.stage == "candidate"
    assert note.category == "hypothesis"
    assert store.learning_is_current(note, identity)
    _ = store.memory.review(
        note.note_id, identity, expected_sha256=contract_sha256(note), stage="review", now=NOW
    )
    reviewed = store.memory.get(note.note_id, identity)
    assert reviewed is not None
    approved = store.memory.review(
        note.note_id, identity, expected_sha256=contract_sha256(reviewed), stage="approved", now=NOW
    )
    _ = store.record(
        measurement(identity, observation_id="fixed", supersedes="first", elapsed_minutes=15),
        identity,
    )
    assert not store.learning_is_current(approved, identity)
    assert not store.learning_is_current(approved, access(workspace="other"))
    with pytest.raises(ValueError, match="current_observations_required"):
        _ = store.learning_candidate(
            identity,
            note_id="stale",
            observation_ids=("first",),
            observation="stale",
            counterexample="none known",
            applicability="this work",
            now=NOW,
            expires_at=NOW + timedelta(days=1),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"window_start": NOW.replace(tzinfo=None)},
        {"window_end": NOW + timedelta(seconds=1)},
        {"window_start": NOW - timedelta(minutes=12)},
        {"elapsed_minutes": float("nan")},
        {"evidence_status": "system_verified"},
    ],
)
def test_time_and_evidence_boundary(changes: dict[str, object]) -> None:
    with pytest.raises(
        ValueError, match=r"(requires_utc|window_invalid|equal_endpoints|finite|literal_error)"
    ):
        _ = measurement(access(), **changes)


def test_correction_requires_original_author_or_reviewer(tmp_path: Path) -> None:
    store = WorkObservationStore(tmp_path / "state.db")
    identity = access()
    _ = store.record(measurement(identity), identity)
    peer = identity.model_copy(update={"actor_id": "peer", "can_review": False})
    correction = measurement(peer, observation_id="correction", supersedes="first")
    with pytest.raises(ValueError, match="correction_author_denied"):
        _ = store.record(correction, peer)
    reviewer = peer.model_copy(update={"can_review": True})
    assert store.record(correction, reviewer).author_id == "peer"


def test_learning_replay_keeps_original_timestamps_and_stage(tmp_path: Path) -> None:
    store = WorkObservationStore(tmp_path / "state.db")
    identity = access()
    _ = store.record(measurement(identity), identity)
    first = store.learning_candidate(
        identity,
        note_id="replay",
        observation_ids=("first",),
        observation="reported 12 minutes",
        counterexample="no comparison",
        applicability="Japanese example",
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    reviewed = store.memory.review(
        first.note_id, identity, expected_sha256=contract_sha256(first), stage="review", now=NOW
    )
    approved = store.memory.review(
        first.note_id,
        identity,
        expected_sha256=contract_sha256(reviewed),
        stage="approved",
        now=NOW,
    )
    replay = store.learning_candidate(
        identity,
        note_id="replay",
        observation_ids=("first",),
        observation="reported 12 minutes",
        counterexample="no comparison",
        applicability="Japanese example",
        now=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(days=2),
    )
    assert replay == approved
    with pytest.raises(ValueError, match="idempotency_conflict"):
        _ = store.learning_candidate(
            identity,
            note_id="replay",
            observation_ids=("first",),
            observation="different",
            counterexample="no comparison",
            applicability="Japanese example",
            now=NOW,
            expires_at=NOW + timedelta(days=1),
        )


def test_work_record_limit_preserves_idempotent_replay(tmp_path: Path) -> None:
    store = WorkObservationStore(tmp_path / "state.db")
    identity = access()
    for index in range(1000):
        _ = store.record(measurement(identity, observation_id=f"record-{index}"), identity)
    assert store.record(measurement(identity, observation_id="record-0"), identity)
    with pytest.raises(ValueError, match="limit_reached"):
        _ = store.record(measurement(identity, observation_id="excess"), identity)
    assert len(store.summarize(identity).observations) == 1000

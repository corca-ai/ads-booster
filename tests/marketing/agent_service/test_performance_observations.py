"""Reported channel outcomes stay scoped, corrigible, bounded and non-causal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope
from ads_booster.contracts.performance_observation import PerformanceObservation
from ads_booster.marketing.agent_service.performance_observations import PerformanceObservationStore

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 8, tzinfo=UTC)


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


def observation(identity: MemoryAccess, **updates: object) -> PerformanceObservation:
    return PerformanceObservation.model_validate(
        {
            "observation_id": "first",
            "scope": identity.scope.model_dump(),
            "author_id": identity.actor_id,
            "source_ref": "slack:thread:message",
            "source_sha256": "a" * 64,
            "channel": "threads",
            "account_id": "trace-jp",
            "country": "JP",
            "publication_ref": "https://example.test/post/1",
            "window_start": NOW - timedelta(days=1),
            "window_end": NOW,
            "recorded_at": NOW,
            "views": 100,
            "likes": 8,
            "comments": 2,
            **updates,
        }
    )


def test_correction_and_restart_preserve_original_without_double_snapshot(tmp_path: Path) -> None:
    store = PerformanceObservationStore(tmp_path / "db")
    actor = access()
    first = observation(actor)
    assert store.record(first, actor) == store.record(first, actor)
    with pytest.raises(ValueError, match="idempotency_conflict"):
        _ = store.record(observation(actor, views=9), actor)
    corrected = observation(actor, observation_id="second", supersedes="first", views=90)
    _ = store.record(corrected, actor)
    restarted = PerformanceObservationStore(tmp_path / "db")
    assert restarted.get("first", actor) == first
    assert restarted.list(actor) == (corrected,)
    assert restarted.list(actor, current_only=False) == (first, corrected)
    with pytest.raises(ValueError, match="already_corrected"):
        _ = store.record(observation(actor, observation_id="fork", supersedes="first"), actor)
    with pytest.raises(ValueError, match="current_observations"):
        _ = store.compare(("first", "second"), actor)


def test_tenant_work_private_scope_and_correction_author_are_enforced(tmp_path: Path) -> None:
    store = PerformanceObservationStore(tmp_path / "db")
    owner = access(private=True)
    first = observation(owner)
    _ = store.record(first, owner)
    for stranger in (
        access(),
        access(workspace="other", private=True),
        access(work="other", private=True),
    ):
        assert store.get("first", stranger) is None
        assert store.list(stranger) == ()
        with pytest.raises(ValueError, match="scope_or_author_denied"):
            _ = store.record(first, stranger)
    with pytest.raises(ValueError, match="correction_scope_denied"):
        _ = store.record(observation(access(), observation_id="copy", supersedes="first"), access())
    shared = access()
    _ = store.record(observation(shared), shared)
    nonreviewer = shared.model_copy(update={"actor_id": "another", "can_review": False})
    with pytest.raises(ValueError, match="correction_author_denied"):
        _ = store.record(
            observation(nonreviewer, observation_id="edit", supersedes="first"), nonreviewer
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_id", "other"),
        ("country", "US"),
        ("channel", "instagram"),
        ("window_start", NOW - timedelta(days=2)),
        ("window_end", NOW - timedelta(hours=1)),
    ],
)
def test_comparison_retains_incomparable_snapshots_without_aggregation(
    tmp_path: Path, field: str, value: object
) -> None:
    store = PerformanceObservationStore(tmp_path / "db")
    actor = access()
    _ = store.record(observation(actor), actor)
    second = observation(actor, observation_id="second", **{field: value})
    _ = store.record(second, actor)
    comparison = store.compare(("first", "second"), actor)
    assert comparison.comparable is False
    assert comparison.mismatch_reasons == (field + "_mismatch",)
    assert len(comparison.observations) == 2
    assert "aggregation" in comparison.interpretation
    assert "total_views" not in comparison.model_dump()


def test_comparable_publications_still_report_individual_unknown_counts(tmp_path: Path) -> None:
    store = PerformanceObservationStore(tmp_path / "db")
    actor = access()
    _ = store.record(observation(actor), actor)
    _ = store.record(observation(actor, observation_id="second", publication_ref="post-2"), actor)
    comparison = store.compare(("second", "first"), actor)
    assert comparison.comparable
    assert comparison.observations[0].clicks is None
    assert comparison.evidence_status == "human_reported"
    with pytest.raises(ValueError, match="list_limit"):
        _ = store.list(actor, limit=101)


def test_learning_candidate_has_current_scoped_sources_and_corrections_invalidate(
    tmp_path: Path,
) -> None:
    store = PerformanceObservationStore(tmp_path / "db")
    actor = access(private=True)
    _ = store.record(observation(actor), actor)
    note = store.learning_candidate(
        actor,
        note_id="lesson",
        observation_ids=("first",),
        observation="Useful signal",
        counterexample="One report",
        applicability="JP account only",
        now=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    assert note.stage == "candidate"
    assert note.category == "hypothesis"
    assert store.learning_is_current(note, actor)
    assert not store.learning_is_current(note, access())
    _ = store.record(
        observation(actor, observation_id="correction", supersedes="first", views=2), actor
    )
    assert not PerformanceObservationStore(tmp_path / "db").learning_is_current(note, actor)
    assert store.memory.get("lesson", actor) == note
    with pytest.raises(ValueError, match="current_observations"):
        _ = store.learning_candidate(
            actor,
            note_id="later",
            observation_ids=("first",),
            observation="X",
            counterexample="Y",
            applicability="Z",
            now=NOW,
            expires_at=NOW + timedelta(days=7),
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"views": -1},
        {"views": True},
        {"views": 1.2},
        {"clicks": 10**12 + 1},
        {"window_start": NOW},
        {"window_end": NOW + timedelta(seconds=1)},
        {"recorded_at": NOW.replace(tzinfo=None)},
        {"evidence_status": "provider_verified"},
        {"country": ""},
    ],
)
def test_invalid_counts_windows_or_verification_claims_are_rejected(
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _ = observation(access(), **updates)


def test_bounded_listing_keeps_latest_report_and_stable_ties_after_restart(tmp_path: Path) -> None:
    store = PerformanceObservationStore(tmp_path / "db")
    actor = access()
    for index, identifier in enumerate(("a", "b", "c", "d", "e", "f", "z")):
        _ = store.record(
            observation(
                actor, observation_id=identifier, recorded_at=NOW + timedelta(seconds=index)
            ),
            actor,
        )
    # Equal timestamps have a stable ID tie-break; insertion order is not authoritative.
    _ = store.record(
        observation(actor, observation_id="y", recorded_at=NOW + timedelta(seconds=6)), actor
    )
    _ = store.record(
        observation(
            actor, observation_id="x", recorded_at=NOW + timedelta(seconds=6, microseconds=1)
        ),
        actor,
    )
    current = PerformanceObservationStore(tmp_path / "db").list(actor, limit=6)
    assert tuple(item.observation_id for item in current) == ("x", "y", "z", "f", "e", "d")
    comparison = store.compare(("z", "a"), actor)
    assert tuple(item.observation_id for item in comparison.observations) == ("a", "z")

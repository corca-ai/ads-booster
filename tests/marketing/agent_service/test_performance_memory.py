"""Human-approved metric interpretations remain dependent on current scoped evidence."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.marketing.agent_service.memory import SQLiteMemoryStore
from ads_booster.marketing.agent_service.performance_observations import PerformanceObservationStore
from tests.marketing.agent_service.test_performance_observations import NOW, access, observation

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.agent_memory import MemoryAccess, MemoryNote


def candidate(root: Path, actor: MemoryAccess) -> MemoryNote:
    store = PerformanceObservationStore(root / "db")
    _ = store.record(observation(actor), actor)
    return store.learning_candidate(
        actor,
        note_id="metric-learning",
        observation_ids=("first",),
        observation="Observed interest",
        counterexample="Accounts differ",
        applicability="Japan test only",
        now=NOW,
        expires_at=NOW + timedelta(days=30),
    )


def test_adopted_metrics_learning_disappears_after_source_correction(tmp_path: Path) -> None:
    actor = access()
    note = candidate(tmp_path, actor)
    memory = SQLiteMemoryStore(tmp_path / "db")
    for stage in ("review", "approved"):
        note = memory.review(
            note.note_id, actor, expected_sha256=contract_sha256(note), stage=stage, now=NOW
        )
    selected = memory.select(actor, query="interest", run_id=actor.scope.work_id, now=NOW)
    assert selected.notes == (note,)
    _ = PerformanceObservationStore(tmp_path / "db").record(
        observation(actor, observation_id="corrected", supersedes="first", views=9), actor
    )
    current = memory.select(actor, query="interest", run_id=actor.scope.work_id, now=NOW)
    assert current.notes == ()
    assert current.receipt.selected == ()


def test_candidate_cannot_be_reviewed_or_approved_after_correction(tmp_path: Path) -> None:
    actor = access()
    note = candidate(tmp_path, actor)
    memory = SQLiteMemoryStore(tmp_path / "db")
    review = memory.review(
        note.note_id, actor, expected_sha256=contract_sha256(note), stage="review", now=NOW
    )
    _ = PerformanceObservationStore(tmp_path / "db").record(
        observation(actor, observation_id="corrected", supersedes="first", views=9), actor
    )
    with pytest.raises(ValueError, match="source_not_current"):
        _ = memory.review(
            review.note_id,
            actor,
            expected_sha256=contract_sha256(review),
            stage="approved",
            now=NOW,
        )


@pytest.mark.parametrize("private", [False, True])
def test_forged_or_crosswork_learning_sources_fail_closed(tmp_path: Path, private: bool) -> None:
    actor = access(private=private)
    note = candidate(tmp_path, actor)
    memory = SQLiteMemoryStore(tmp_path / "db")
    forged = note.model_copy(update={"note_id": "forged", "source_sha256": "f" * 64})
    memory.put(forged, actor, now=NOW)
    with pytest.raises(ValueError, match="source_not_current"):
        _ = memory.review(
            forged.note_id, actor, expected_sha256=contract_sha256(forged), stage="review", now=NOW
        )
    other = access(work="other", private=private)
    copied = note.model_copy(update={"note_id": "copied", "scope": other.scope})
    memory.put(copied, other, now=NOW)
    with pytest.raises(ValueError, match="source_not_current"):
        _ = memory.review(
            copied.note_id, other, expected_sha256=contract_sha256(copied), stage="review", now=NOW
        )
    assert memory.get(note.note_id, access(workspace="stranger", private=private)) is None

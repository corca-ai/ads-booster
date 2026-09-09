"""Channel authority fences reusable notes and reported observations in real SQLite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryScope
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.learning.memory import SQLiteMemoryStore
from ads_booster.learning.performance_observations import PerformanceObservationStore
from ads_booster.learning.work_observations import WorkObservationStore
from tests.marketing.agent_service.test_memory import NOW, access, approve, note, selected
from tests.marketing.agent_service.test_performance_observations import observation
from tests.marketing.agent_service.test_work_observations import measurement

if TYPE_CHECKING:
    from pathlib import Path


def channel_access(channel_id: str | None, *, work_id: str = "") -> MemoryAccess:
    return MemoryAccess(
        scope=MemoryScope(
            workspace_id="trace",
            product_id="trace",
            work_id=work_id,
            channel_id=channel_id,
        ),
        actor_id="reviewer",
        can_review=True,
    )


def test_channel_read_excludes_foreign_and_legacy_memory_after_restart(tmp_path: Path) -> None:
    # Given identical project/query scopes with different channel authority and legacy data.
    database = tmp_path / "memory.sqlite"
    store = SQLiteMemoryStore(database)
    for name, owner in (
        ("channel-a", channel_access("C-A")),
        ("channel-b", channel_access("C-B")),
        ("legacy", access(product="trace")),
    ):
        _ = approve(store, note(name, owner), owner)

    # When the persisted store is opened for a new work in channel A.
    restarted = SQLiteMemoryStore(database)
    actor = channel_access("C-A", work_id="new-thread-run")
    selection = selected(restarted, actor)

    # Then only channel A's shared memory is available through every read surface.
    assert selection == ["channel-a"]
    assert [item.note_id for item in restarted.list_notes(actor)] == ["channel-a"]
    assert restarted.get("channel-b", actor) is None
    assert restarted.get("legacy", actor) is None
    assert selected(restarted, channel_access(None)) == ["legacy"]


def test_channel_cannot_write_review_or_delete_foreign_memory(tmp_path: Path) -> None:
    # Given channel B owns an approved note.
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    owner = channel_access("C-B")
    item = approve(store, note("foreign", owner), owner)
    actor = channel_access("C-A")

    # When another channel tries to act using the exact ID and digest.
    # Then neither a write nor a review/delete acquires authority from those identifiers.
    with pytest.raises(ValueError, match="write_scope_denied"):
        store.put(note("copy", owner), actor, now=NOW)
    with pytest.raises(ValueError, match="memory_not_found"):
        _ = store.review(
            item.note_id, actor, expected_sha256=contract_sha256(item), stage="rejected", now=NOW
        )
    with pytest.raises(ValueError, match="memory_not_found"):
        store.delete(item.note_id, actor, expected_sha256=contract_sha256(item), now=NOW)
    assert store.get(item.note_id, owner) == item


@pytest.mark.parametrize("kind", ["work", "performance"])
def test_observations_keep_channel_keys_and_exclude_legacy_after_restart(
    tmp_path: Path, kind: str
) -> None:
    # Given same workspace, project, work and observation ID in three different scopes.
    database = tmp_path / "memory.sqlite"
    actors = tuple(channel_access(channel, work_id="same-run") for channel in (None, "C-A", "C-B"))
    if kind == "work":
        store = WorkObservationStore(database)
        for actor in actors:
            _ = store.record(measurement(actor), actor)
        # When the work store restarts, each key retains exactly its own observation.
        restarted = WorkObservationStore(database)
        for actor in actors:
            assert restarted.summarize(actor).observations == (measurement(actor),)
    else:
        performance = PerformanceObservationStore(database)
        for actor in actors:
            _ = performance.record(observation(actor), actor)
        # When the performance store restarts, each key retains exactly its own observation.
        reopened = PerformanceObservationStore(database)
        for actor in actors:
            assert reopened.list(actor) == (observation(actor),)


def test_absent_channel_preserves_existing_scope_json_and_nested_digest() -> None:
    # Given an existing persisted scope with no channel field.
    legacy = (
        '{"workspace_id":"trace","product_id":"","campaign_id":"","work_id":"",'
        '"member_id":"","session_id":""}'
    )
    expected_digest = contract_sha256(
        {
            "scope": {
                "workspace_id": "trace",
                "product_id": "",
                "campaign_id": "",
                "work_id": "",
                "member_id": "",
                "session_id": "",
            }
        }
    )

    # When the new contract parses it and serializes directly or nested inside an access.
    scope = MemoryScope.model_validate_json(legacy)
    serialized = scope.model_dump_json()

    # Then old observation keys and source/review digests remain byte equivalent.
    assert serialized == legacy
    assert contract_sha256({"scope": scope.model_dump(mode="json")}) == expected_digest
    assert "channel_id" not in MemoryAccess(scope=scope, actor_id="reviewer").model_dump_json()


def test_channel_identity_does_not_replace_private_member_and_session(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    owner = MemoryAccess(
        scope=MemoryScope(
            workspace_id="trace", channel_id="D1", member_id="alice", session_id="session-a"
        ),
        actor_id="alice",
        private=True,
        can_review=True,
    )
    _ = approve(store, note("private", owner), owner)
    for member, session in (("bob", "session-a"), ("alice", "session-b")):
        stranger = MemoryAccess(
            scope=owner.scope.model_copy(update={"member_id": member, "session_id": session}),
            actor_id=member,
            private=True,
        )
        assert selected(store, stranger) == []
    assert selected(store, owner) == ["private"]

"""Real SQLite privacy, correction and human review boundaries for reusable notes."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from pydantic import TypeAdapter

from ads_booster.contracts.agent_memory import MemoryAccess, MemoryNote, MemoryScope
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.learning.memory import SQLiteMemoryStore

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def access(  # noqa: PLR0913 - independently exercise every authorization scope.
    *,
    workspace: str = "trace",
    product: str = "",
    campaign: str = "",
    member: str = "",
    session: str = "",
    reviewer: bool = True,
) -> MemoryAccess:
    return MemoryAccess(
        scope=MemoryScope(
            workspace_id=workspace,
            product_id=product,
            campaign_id=campaign,
            member_id=member,
            session_id=session,
        ),
        actor_id=member or "reviewer",
        private=bool(member),
        can_review=reviewer,
    )


def note(  # noqa: PLR0913 - explicit source and correction fixture.
    note_id: str,
    actor: MemoryAccess,
    *,
    text: str = "calendar contrast preference",
    version: int = 1,
    supersedes: str = "",
    conflicts: tuple[str, ...] = (),
) -> MemoryNote:
    return MemoryNote(
        note_id=note_id,
        scope=actor.scope,
        category="preference",
        domain="asset_format",
        text=text,
        source_ref="synthetic:team-review",
        source_sha256="a" * 64,
        author_id=actor.actor_id,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
        version=version,
        supersedes=supersedes,
        conflicts=conflicts,
    )


def approve(store: SQLiteMemoryStore, item: MemoryNote, actor: MemoryAccess) -> MemoryNote:
    store.put(item, actor, now=NOW)
    reviewed = store.review(
        item.note_id, actor, expected_sha256=contract_sha256(item), stage="review", now=NOW
    )
    return store.review(
        item.note_id, actor, expected_sha256=contract_sha256(reviewed), stage="approved", now=NOW
    )


def selected(store: SQLiteMemoryStore, actor: MemoryAccess, *, now: datetime = NOW) -> list[str]:
    return [n.note_id for n in store.select(actor, query="calendar", run_id="run-1", now=now).notes]


def test_shared_and_private_reads_filter_before_relevance(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    shared = access()
    private = access(member="alice", session="s1")
    for name, owner in [
        ("shared", shared),
        ("own", private),
        ("other_member", access(member="bob", session="s1")),
        ("other_session", access(member="alice", session="s2")),
        ("other_workspace", access(workspace="other")),
        ("other_product", access(product="other")),
        ("other_campaign", access(campaign="other")),
    ]:
        _ = approve(store, note(name, owner), owner)
    assert set(selected(store, private)) == {"shared", "own"}
    assert selected(store, shared) == ["shared"]


def test_private_cannot_promote_review_or_delete_shared_memory(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    shared = access()
    private = access(member="alice", session="s1")
    item = approve(store, note("shared", shared), shared)
    with pytest.raises(ValueError, match="shared_read_only"):
        store.put(note("promoted", shared), private, now=NOW)
    with pytest.raises(ValueError, match="shared_read_only"):
        _ = store.review(
            item.note_id, private, expected_sha256=contract_sha256(item), stage="rejected", now=NOW
        )
    with pytest.raises(ValueError, match="shared_read_only"):
        store.delete(item.note_id, private, expected_sha256=contract_sha256(item), now=NOW)
    assert selected(store, private) == ["shared"]


def test_human_review_stale_digest_and_approval_is_data_only(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    actor = access()
    item = note("decision", actor).model_copy(update={"category": "approval_decision"})
    store.put(item, actor, now=NOW)
    assert selected(store, actor) == []
    with pytest.raises(ValueError, match="review_not_allowed"):
        _ = store.review(
            item.note_id,
            access(reviewer=False),
            expected_sha256=contract_sha256(item),
            stage="review",
            now=NOW,
        )
    with pytest.raises(ValueError, match="transition_invalid"):
        _ = store.review(
            item.note_id, actor, expected_sha256=contract_sha256(item), stage="approved", now=NOW
        )
    reviewed = store.review(
        item.note_id, actor, expected_sha256=contract_sha256(item), stage="review", now=NOW
    )
    with pytest.raises(ValueError, match="revision_changed"):
        _ = store.review(
            item.note_id, actor, expected_sha256=contract_sha256(item), stage="approved", now=NOW
        )
    _ = store.review(
        item.note_id, actor, expected_sha256=contract_sha256(reviewed), stage="approved", now=NOW
    )
    selection = store.select(actor, query="calendar", run_id="run-1", now=NOW)
    assert selection.receipt.authority == "data_only_not_execution_approval"
    assert selection.receipt.selected[0].sha256 == contract_sha256(selection.notes[0])


def test_correction_expiry_and_delete_never_resurrect_old_rule(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    store = SQLiteMemoryStore(path)
    actor = access()
    _ = approve(store, note("v1", actor), actor)
    correction = note("v2", actor, version=2, supersedes="v1")
    store.put(correction, actor, now=NOW)
    assert selected(store, actor) == ["v1"]
    correction = approve(store, correction, actor)
    assert selected(SQLiteMemoryStore(path), actor) == ["v2"]
    assert selected(store, actor, now=NOW + timedelta(days=2)) == []
    store.delete("v2", actor, expected_sha256=contract_sha256(correction), now=NOW)
    assert selected(SQLiteMemoryStore(path), actor) == []
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_memory_history").fetchone()[0] == 7
        assert db.execute("SELECT COUNT(*) FROM agent_memory_selections").fetchone()[0] == 4


def test_conflict_scope_and_correction_version_cannot_be_laundered(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    actor = access()
    _ = approve(store, note("v1", actor), actor)
    with pytest.raises(ValueError, match="version_invalid"):
        store.put(note("v2", actor, supersedes="v1"), actor, now=NOW)
    private = access(member="alice", session="s1")
    with pytest.raises(ValueError, match="reference_scope_denied"):
        store.put(note("private", private, conflicts=("v1",)), private, now=NOW)
    counter = approve(store, note("counter", actor, conflicts=("v1",)), actor)
    assert counter.conflicts == ("v1",)
    assert set(selected(store, actor)) == {"v1", "counter"}


def test_relevance_and_budget_are_bounded_with_durable_provenance(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    actor = access()
    _ = approve(store, note("calendar", actor), actor)
    _ = approve(store, note("unrelated", actor, text="paid budget"), actor)
    selection = store.select(actor, query="calendar", run_id="run-1", now=NOW, limit=1)
    assert [n.note_id for n in selection.notes] == ["calendar"]
    assert store.select(actor, query="calendar", run_id="run-1", now=NOW, max_chars=1).notes == ()
    with store.connect() as db:
        raw = TypeAdapter(str).validate_python(
            db.execute(
                "SELECT data_json FROM agent_memory_selections WHERE selection_id=?",
                (selection.receipt.selection_id,),
            ).fetchone()[0]
        )
        assert "calendar contrast preference" not in raw
        assert selection.receipt.query_sha256 in raw
    with pytest.raises(ValueError, match="budget_invalid"):
        _ = store.select(actor, query="calendar", run_id="run-1", now=NOW, limit=25)


def test_parallel_corrections_require_current_head_and_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    store = SQLiteMemoryStore(path)
    actor = access()
    _ = approve(store, note("original", actor), actor)
    left = note("left", actor, version=2, supersedes="original")
    right = note("right", actor, version=2, supersedes="original")
    store.put(right, actor, now=NOW)
    right = store.review(
        "right", actor, expected_sha256=contract_sha256(right), stage="review", now=NOW
    )
    _ = approve(store, left, actor)
    restarted = SQLiteMemoryStore(path)
    with pytest.raises(ValueError, match="correction_head_changed"):
        _ = restarted.review(
            "right", actor, expected_sha256=contract_sha256(right), stage="approved", now=NOW
        )
    assert selected(restarted, actor) == ["left"]
    pending = restarted.get("right", actor)
    assert pending is not None
    assert pending.stage == "review"
    assert restarted.get("right", access(workspace="other")) is None


def test_image_preference_stays_in_its_work_and_cannot_widen(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    shared = access(product="trace")
    first = shared.model_copy(
        update={"scope": shared.scope.model_copy(update={"work_id": "run-a"})}
    )
    second = shared.model_copy(
        update={"scope": shared.scope.model_copy(update={"work_id": "run-b"})}
    )
    _ = approve(store, note("brand", shared), shared)
    local = approve(store, note("image-font", first), first)
    assert set(selected(store, first)) == {"brand", "image-font"}
    assert selected(store, second) == ["brand"]
    assert selected(store, shared) == ["brand"]
    with pytest.raises(ValueError, match="write_scope_denied"):
        store.put(note("widened", shared), first, now=NOW)
    with pytest.raises(ValueError, match="not_found"):
        store.delete(local.note_id, second, expected_sha256=contract_sha256(local), now=NOW)

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import TYPE_CHECKING

from ads_booster.contracts.run import TraceRunEvent
from ads_booster.runtime.trace_run import (
    IdempotencyConflictError,
    TraceRunRequest,
    TraceRunState,
)
from ads_booster.runtime.trace_run_store import JsonlTraceRunStore, TraceRunRecord

from .test_trace_run import make_request

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def idempotency_conflict_from(
    action: Callable[[], TraceRunRecord],
) -> IdempotencyConflictError | None:
    try:
        _ = action()
    except IdempotencyConflictError as error:
        return error
    return None


def test_store_when_two_callers_transition_the_same_snapshot_then_it_never_duplicates_sequence(
    tmp_path: Path,
) -> None:
    # Given two callers holding the same durable run snapshot
    store = JsonlTraceRunStore(root=tmp_path / "state")
    queued = store.begin(make_request())
    _ = store.transition(record=queued, state=TraceRunState.RUNNING)

    # When the stale caller tries to append the same transition
    with suppress(RuntimeError):
        _ = store.transition(record=queued, state=TraceRunState.RUNNING)

    # Then the journal remains a contiguous single-writer history
    journal = tmp_path / "state" / "run-01" / "transitions.jsonl"
    events = [TraceRunEvent.model_validate_json(line) for line in journal.read_text().splitlines()]
    assert [event.sequence for event in events] == [0, 1]


def test_store_when_same_run_id_has_different_input_then_it_rejects_conflict(
    tmp_path: Path,
) -> None:
    # Given a stored run identity
    store = JsonlTraceRunStore(root=tmp_path / "state")
    _ = store.begin(make_request())

    # When a caller reuses that identity with a different valid request
    changed = make_request()
    changed_payload = changed.model_copy(update={"run_id": "run-01"})
    changed_payload = changed_payload.model_copy(
        update={"composite_job": changed.composite_job.model_copy(update={"job_id": "compose-02"})}
    )

    # Then the store rejects the idempotency conflict before a tool can run
    error = idempotency_conflict_from(lambda: store.begin(changed_payload))
    assert error is not None
    assert error.run_id == "run-01"


def test_store_when_idempotency_key_crosses_run_directories_then_it_rejects_conflict(
    tmp_path: Path,
) -> None:
    # Given a stored idempotency key bound to one run directory
    store = JsonlTraceRunStore(root=tmp_path / "state")
    first = make_request(run_id="run-01").model_copy(update={"idempotency_key": "shared-key"})
    _ = store.begin(first)
    second = make_request(run_id="run-02").model_copy(update={"idempotency_key": "shared-key"})

    # When another run directory tries to claim that key
    error = idempotency_conflict_from(lambda: store.begin(second))

    # Then the store rejects the cross-run claim while preserving the first binding
    assert error is not None
    assert error.run_id == "run-02"
    assert (tmp_path / "state" / "run-01" / "transitions.jsonl").is_file()
    assert not (tmp_path / "state" / "run-02" / "transitions.jsonl").exists()


def test_store_when_different_keys_create_different_runs_then_both_bindings_survive(
    tmp_path: Path,
) -> None:
    # Given two run requests with distinct keys
    store = JsonlTraceRunStore(root=tmp_path / "state")
    first = make_request(run_id="run-01")
    second = make_request(run_id="run-02")

    # When each request begins under the same store root
    first_record = store.begin(first)
    second_record = store.begin(second)

    # Then each run remains independently replayable
    assert first_record.run_id == "run-01"
    assert second_record.run_id == "run-02"
    assert first_record.idempotency_key != second_record.idempotency_key


def test_store_when_a_sibling_has_legacy_event_fields_then_a_new_key_can_start(
    tmp_path: Path,
) -> None:
    # Given a legacy sibling journal with a valid identity but obsolete later event fields
    root = tmp_path / "state"
    store = JsonlTraceRunStore(root=root)
    _ = store.begin(make_request(run_id="legacy-run"))
    journal = root / "legacy-run" / "transitions.jsonl"
    first_line = journal.read_text(encoding="utf-8").splitlines()[0]
    legacy_line = first_line.replace(
        '"capture_provenance":null',
        '"capture_provenance":{"source":"offline_fixture"}',
    )
    _ = journal.write_text(f"{first_line}\n{legacy_line}\n", encoding="utf-8")

    # When an unrelated idempotency key starts a new run
    record = store.begin(make_request(run_id="new-run"))

    # Then the legacy non-identity fields do not block the independent run
    assert record.run_id == "new-run"
    assert record.state is TraceRunState.QUEUED


def test_store_when_same_key_begins_concurrently_then_compare_and_scan_allows_one_binding(
    tmp_path: Path,
) -> None:
    # Given two store instances racing to claim one key in sibling run directories
    root = tmp_path / "state"
    first = make_request(run_id="run-01").model_copy(update={"idempotency_key": "shared-key"})
    second = make_request(run_id="run-02").model_copy(update={"idempotency_key": "shared-key"})

    def begin(request: TraceRunRequest) -> str:
        try:
            _ = JsonlTraceRunStore(root=root).begin(request)
        except IdempotencyConflictError:
            return "conflict"
        return "started"

    # When both callers begin at the same time
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(begin, (first, second)))

    # Then exactly one durable binding wins the compare-and-scan race
    assert sorted(outcomes) == ["conflict", "started"]

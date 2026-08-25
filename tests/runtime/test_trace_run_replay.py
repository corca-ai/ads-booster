from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from trace_capture.contracts.run import TraceRunEvent
from trace_capture.runtime.trace_run import (
    ComposeCompleted,
    TraceRunCapability,
    TraceRunRunner,
    TraceRunState,
)
from trace_capture.runtime.trace_run_store import (
    InvalidRunJournalError,
    JsonlTraceRunStore,
    TraceRunRecord,
)

from .test_trace_run import (
    CallAccumulator,
    RecordingCapturePort,
    RecordingComposePort,
    make_request,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from trace_capture.contracts import MarketingCompositeJob


def invalid_journal_error_from(
    action: Callable[[], TraceRunRecord],
) -> InvalidRunJournalError | None:
    try:
        _ = action()
    except InvalidRunJournalError as error:
        return error
    return None


def test_store_when_journal_sequence_is_not_contiguous_then_replay_fails_closed(
    tmp_path: Path,
) -> None:
    # Given an otherwise valid durable journal whose first event has an out-of-order sequence
    request = make_request()
    store = JsonlTraceRunStore(root=tmp_path / "state")
    _ = store.begin(request)
    journal = tmp_path / "state" / "run-01" / "transitions.jsonl"
    event = TraceRunEvent.model_validate_json(journal.read_text(encoding="utf-8"))
    corrupted = event.model_copy(update={"sequence": 7})
    _ = journal.write_text(corrupted.model_dump_json() + "\n", encoding="utf-8")

    # When a fresh process attempts to replay the run
    error = invalid_journal_error_from(lambda: store.begin(request))
    assert error is not None
    assert error.run_id == "run-01"

    # Then no capability can be recovered from the corrupt journal


def test_store_when_journal_digest_changes_then_replay_fails_closed(tmp_path: Path) -> None:
    # Given a journal whose later transition carries a different input digest
    request = make_request()
    store = JsonlTraceRunStore(root=tmp_path / "state")
    first = store.begin(request)
    _ = store.transition(record=first, state=TraceRunState.RUNNING)
    journal = tmp_path / "state" / "run-01" / "transitions.jsonl"
    events = [
        TraceRunEvent.model_validate_json(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
    ]
    events[1] = events[1].model_copy(update={"input_digest": "f" * 64})
    _ = journal.write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n",
        encoding="utf-8",
    )

    # When a fresh process attempts to replay the altered journal
    error = invalid_journal_error_from(lambda: store.begin(request))
    assert error is not None
    assert error.run_id == "run-01"

    # Then the changed digest is rejected before tools can be invoked


def test_store_when_journal_idempotency_key_changes_then_replay_fails_closed(
    tmp_path: Path,
) -> None:
    # Given a journal whose later transition carries a different idempotency key
    request = make_request()
    store = JsonlTraceRunStore(root=tmp_path / "state")
    first = store.begin(request)
    _ = store.transition(record=first, state=TraceRunState.RUNNING)
    journal = tmp_path / "state" / "run-01" / "transitions.jsonl"
    events = [
        TraceRunEvent.model_validate_json(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
    ]
    events[1] = events[1].model_copy(update={"idempotency_key": "changed-key"})
    _ = journal.write_text(
        "\n".join(event.model_dump_json() for event in events) + "\n",
        encoding="utf-8",
    )

    # When a fresh process attempts to replay the altered journal
    error = invalid_journal_error_from(lambda: store.begin(request))
    assert error is not None
    assert error.run_id == "run-01"

    # Then the key mutation is rejected before tools can be invoked


def test_store_when_journal_skips_capabilities_then_replay_fails_closed(
    tmp_path: Path,
) -> None:
    # Given a syntactically valid journal that jumps from queued directly to completed
    request = make_request()
    store = JsonlTraceRunStore(root=tmp_path / "state")
    _ = store.begin(request)
    journal = tmp_path / "state" / "run-01" / "transitions.jsonl"
    queued = TraceRunEvent.model_validate_json(journal.read_text(encoding="utf-8"))
    completed = queued.model_copy(update={"state": TraceRunState.COMPLETED})
    _ = journal.write_text(completed.model_dump_json() + "\n", encoding="utf-8")

    # When a fresh process attempts to replay the forged terminal state
    error = invalid_journal_error_from(lambda: store.begin(request))

    # Then it rejects the history before reporting success or invoking tools
    assert error is not None


def test_runner_when_composite_output_path_escapes_then_it_fails_closed(
    tmp_path: Path,
) -> None:
    # Given a valid run whose declared output directory is a symlink
    component = tmp_path / "source-components.png"
    _ = component.write_bytes(b"component")
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "outputs").symlink_to(outside, target_is_directory=True)
    request = make_request()
    capture_log = CallAccumulator()
    compose_log = CallAccumulator()

    # When the runner executes the complete local flow
    result = TraceRunRunner(
        store=JsonlTraceRunStore(root=tmp_path / "state"),
        capture_port=RecordingCapturePort(source=component, call_log=capture_log),
        compose_port=RecordingComposePort(call_log=compose_log),
    ).run(request=request, job_root=tmp_path)

    # Then composition is blocked before the symlink can receive a final image
    assert result.state is TraceRunState.FAILED
    assert not (outside / "final.png").exists()


def test_runner_when_component_changes_after_capture_then_it_rejects_the_mutated_artifact(
    tmp_path: Path,
) -> None:
    # Given a capture completion whose source artifact is durably recorded
    component = tmp_path / "source-components.png"
    _ = component.write_bytes(b"original")
    request = make_request()
    store = JsonlTraceRunStore(root=tmp_path / "state")
    queued = store.begin(request)
    running = store.transition(record=queued, state=TraceRunState.RUNNING)
    awaiting = store.transition(
        record=running,
        state=TraceRunState.AWAITING_TOOL,
        capability=TraceRunCapability.CAPTURE,
    )
    captured = store.transition(
        record=awaiting,
        state=TraceRunState.RUNNING,
        component_artifact=component,
    )
    _ = component.write_bytes(b"mutated")
    capture_log = CallAccumulator()
    compose_log = CallAccumulator()

    # When the runner resumes from the captured running state
    result = TraceRunRunner(
        store=store,
        capture_port=RecordingCapturePort(source=component, call_log=capture_log),
        compose_port=RecordingComposePort(call_log=compose_log),
    ).run(request=request, job_root=tmp_path)

    # Then the changed source is rejected before staging or composition
    assert result.state is TraceRunState.FAILED
    assert capture_log.calls == []
    assert compose_log.calls == []
    assert not (tmp_path / "work" / "trace-components.png").exists()
    assert captured.captured_artifact_sha256 is not None


def test_runner_when_staging_destination_is_a_symlink_then_it_never_writes_outside_job_root(
    tmp_path: Path,
) -> None:
    # Given a job root whose declared staging directory escapes through a symlink
    component = tmp_path / "source-components.png"
    _ = component.write_bytes(b"component")
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "work").symlink_to(outside, target_is_directory=True)
    request = make_request()
    capture_log = CallAccumulator()
    compose_log = CallAccumulator()

    # When the runner executes the local capture path
    result = TraceRunRunner(
        store=JsonlTraceRunStore(root=tmp_path / "state"),
        capture_port=RecordingCapturePort(source=component, call_log=capture_log),
        compose_port=RecordingComposePort(call_log=compose_log),
    ).run(request=request, job_root=tmp_path)

    # Then it fails closed without creating an artifact outside the run root
    assert result.state is TraceRunState.FAILED
    assert not (outside / "trace-components.png").exists()
    assert compose_log.calls == []


def test_runner_when_composer_claims_missing_output_then_it_does_not_report_success(
    tmp_path: Path,
) -> None:
    # Given valid composition inputs and a composer that lies about its output
    component = tmp_path / "source-components.png"
    _ = component.write_bytes(b"component")

    @dataclass(frozen=True, slots=True)
    class MissingOutputComposePort:
        def compose(
            self,
            run_id: str,
            job: MarketingCompositeJob,
            job_root: Path,
        ) -> ComposeCompleted:
            _ = (run_id, job, job_root)
            return ComposeCompleted(output_image=job_root / "outputs" / "missing.png")

    # When the runner invokes the composer
    result = TraceRunRunner(
        store=JsonlTraceRunStore(root=tmp_path / "state"),
        capture_port=RecordingCapturePort(source=component, call_log=CallAccumulator()),
        compose_port=MissingOutputComposePort(),
    ).run(request=make_request(), job_root=tmp_path)

    # Then a missing output is a typed runtime failure, never a completed result
    assert result.state is TraceRunState.FAILED


def test_runner_when_aborted_after_capture_then_stage_and_compose_remain_forbidden(
    tmp_path: Path,
) -> None:
    # Given a capture artifact was recorded before a durable abort transition
    component = tmp_path / "source-components.png"
    _ = component.write_bytes(b"component")
    request = make_request()
    store = JsonlTraceRunStore(root=tmp_path / "state")
    queued = store.begin(request)
    running = store.transition(record=queued, state=TraceRunState.RUNNING)
    awaiting_capture = store.transition(
        record=running,
        state=TraceRunState.AWAITING_TOOL,
        capability=TraceRunCapability.CAPTURE,
    )
    captured = store.transition(
        record=awaiting_capture,
        state=TraceRunState.RUNNING,
        component_artifact=component,
    )
    _ = store.transition(record=captured, state=TraceRunState.ABORTED)
    capture_log = CallAccumulator()
    compose_log = CallAccumulator()
    capture = RecordingCapturePort(source=component, call_log=capture_log)
    compose = RecordingComposePort(call_log=compose_log)

    # When the agent restarts the same run
    result = TraceRunRunner(store=store, capture_port=capture, compose_port=compose).run(
        request=request,
        job_root=tmp_path,
    )

    # Then the terminal abort prevents staging and composition
    assert result.state is TraceRunState.ABORTED
    assert capture_log.calls == []
    assert compose_log.calls == []
    assert not (tmp_path / "work" / "trace-components.png").exists()

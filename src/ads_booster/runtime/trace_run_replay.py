from __future__ import annotations

# pyright: reportUnnecessaryComparison=false
from itertools import pairwise
from typing import assert_never

from ads_booster.contracts.errors import IdempotencyConflictError, InvalidRunJournalError
from ads_booster.contracts.run import (
    TraceRunCapability,
    TraceRunEvent,
    TraceRunRequest,
    TraceRunState,
)


def validate_replay(
    events: tuple[TraceRunEvent, ...],
    request: TraceRunRequest,
    input_digest: str,
) -> None:
    first_event = events[0]
    if (
        first_event.idempotency_key != request.idempotency_key
        or first_event.input_digest != input_digest
    ):
        raise IdempotencyConflictError(run_id=request.run_id)
    prior_timestamp = None
    for sequence, event in enumerate(events):
        consistent = (
            event.run_id == request.run_id
            and event.idempotency_key == first_event.idempotency_key
            and event.input_digest == first_event.input_digest
            and event.sequence == sequence
        )
        monotonic = prior_timestamp is None or event.recorded_at >= prior_timestamp
        if not consistent or not monotonic:
            raise InvalidRunJournalError(run_id=request.run_id)
        prior_timestamp = event.recorded_at
    validate_history(events, request.run_id)


def validate_history(events: tuple[TraceRunEvent, ...], run_id: str) -> None:
    if not events or events[0].state is not TraceRunState.QUEUED:
        raise InvalidRunJournalError(run_id=run_id)
    expected = TraceRunCapability.CAPTURE
    for previous, current in pairwise(events):
        if not transition_is_allowed(previous.state, current.state):
            raise InvalidRunJournalError(run_id=run_id)
        expected = _validate_transition(previous, current, expected, run_id)


def _validate_transition(
    previous: TraceRunEvent,
    current: TraceRunEvent,
    expected: TraceRunCapability,
    run_id: str,
) -> TraceRunCapability:
    match current.state:
        case TraceRunState.RUNNING:
            return _validate_running_transition(previous, current, expected, run_id)
        case TraceRunState.AWAITING_TOOL:
            _require_awaiting(previous, current, expected, run_id)
            return expected
        case TraceRunState.COMPLETED:
            _require_completed(previous, run_id)
            return expected
        case TraceRunState.FAILED:
            return _validate_failed_transition(previous, expected, run_id)
        case TraceRunState.ABORTED:
            _require_aborted(previous, run_id)
            return expected
        case TraceRunState.UNKNOWN_SIDE_EFFECT:
            _require_unknown(previous, current, run_id)
            return expected
        case TraceRunState.QUEUED:
            raise InvalidRunJournalError(run_id=run_id)
        case _ as unreachable:
            assert_never(unreachable)


def _validate_running_transition(
    previous: TraceRunEvent,
    current: TraceRunEvent,
    expected: TraceRunCapability,
    run_id: str,
) -> TraceRunCapability:
    match previous.state:
        case TraceRunState.QUEUED:
            _require_no_component(current, run_id)
            return expected
        case TraceRunState.AWAITING_TOOL:
            if previous.capability is not expected:
                raise InvalidRunJournalError(run_id=run_id)
            match expected:
                case TraceRunCapability.CAPTURE:
                    _require_component(current, run_id)
                    return TraceRunCapability.STAGE_COMPONENTS
                case TraceRunCapability.STAGE_COMPONENTS:
                    _require_no_component(current, run_id)
                    return TraceRunCapability.COMPOSE
                case TraceRunCapability.COMPOSE:
                    raise InvalidRunJournalError(run_id=run_id)
                case _ as unreachable:
                    assert_never(unreachable)
        case (
            TraceRunState.RUNNING
            | TraceRunState.COMPLETED
            | TraceRunState.FAILED
            | TraceRunState.ABORTED
            | TraceRunState.UNKNOWN_SIDE_EFFECT
        ):
            raise InvalidRunJournalError(run_id=run_id)
        case _ as unreachable:
            assert_never(unreachable)


def _validate_failed_transition(
    previous: TraceRunEvent,
    expected: TraceRunCapability,
    run_id: str,
) -> TraceRunCapability:
    if previous.state not in {TraceRunState.RUNNING, TraceRunState.AWAITING_TOOL}:
        raise InvalidRunJournalError(run_id=run_id)
    return expected


def _require_awaiting(
    previous: TraceRunEvent,
    current: TraceRunEvent,
    expected: TraceRunCapability,
    run_id: str,
) -> None:
    if previous.state is not TraceRunState.RUNNING or current.capability is not expected:
        raise InvalidRunJournalError(run_id=run_id)


def _require_completed(previous: TraceRunEvent, run_id: str) -> None:
    if (
        previous.state is not TraceRunState.AWAITING_TOOL
        or previous.capability is not TraceRunCapability.COMPOSE
    ):
        raise InvalidRunJournalError(run_id=run_id)


def _require_aborted(previous: TraceRunEvent, run_id: str) -> None:
    if previous.state not in {
        TraceRunState.QUEUED,
        TraceRunState.RUNNING,
        TraceRunState.AWAITING_TOOL,
        TraceRunState.UNKNOWN_SIDE_EFFECT,
    }:
        raise InvalidRunJournalError(run_id=run_id)


def _require_unknown(
    previous: TraceRunEvent,
    current: TraceRunEvent,
    run_id: str,
) -> None:
    if previous.state is not TraceRunState.AWAITING_TOOL:
        raise InvalidRunJournalError(run_id=run_id)
    if current.capability is not previous.capability:
        raise InvalidRunJournalError(run_id=run_id)


def _require_component(event: TraceRunEvent, run_id: str) -> None:
    if event.component_artifact is None or event.component_artifact_sha256 is None:
        raise InvalidRunJournalError(run_id=run_id)


def _require_no_component(event: TraceRunEvent, run_id: str) -> None:
    if event.component_artifact is not None or event.component_artifact_sha256 is not None:
        raise InvalidRunJournalError(run_id=run_id)


def transition_is_allowed(current: TraceRunState, next_state: TraceRunState) -> bool:
    match current:
        case TraceRunState.QUEUED:
            return next_state in {TraceRunState.RUNNING, TraceRunState.ABORTED}
        case TraceRunState.RUNNING:
            return next_state in {
                TraceRunState.AWAITING_TOOL,
                TraceRunState.FAILED,
                TraceRunState.ABORTED,
            }
        case TraceRunState.AWAITING_TOOL:
            return next_state in {
                TraceRunState.RUNNING,
                TraceRunState.COMPLETED,
                TraceRunState.FAILED,
                TraceRunState.ABORTED,
                TraceRunState.UNKNOWN_SIDE_EFFECT,
            }
        case TraceRunState.UNKNOWN_SIDE_EFFECT:
            return next_state is TraceRunState.ABORTED
        case TraceRunState.COMPLETED | TraceRunState.FAILED | TraceRunState.ABORTED:
            return False
        case _ as unreachable:
            assert_never(unreachable)


__all__ = ["transition_is_allowed", "validate_history", "validate_replay"]

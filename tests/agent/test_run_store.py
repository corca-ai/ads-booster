from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.runs import (
    AgentGoal,
    AgentInput,
    AgentObservation,
    AgentReview,
    AgentRun,
    AgentRunAlreadyExistsError,
    AgentRunId,
    AgentRunResumer,
    AgentRunRevisionError,
    AgentRunState,
    AgentRunStore,
    AgentRunTransitionError,
    AgentRunUpdate,
    ConnectorId,
    ObservationKind,
    ToolPolicy,
)

if TYPE_CHECKING:
    from pathlib import Path


def run() -> AgentRun:
    return AgentRun(
        run_id=AgentRunId("run-1"),
        connector_id=ConnectorId("trace-marketing"),
        connector_version="1.0.0",
        goal=AgentGoal(
            objective="Create one marketing image",
            success_criteria=("artifact awaits review",),
            context={"campaign_id": "campaign-1"},
        ),
        tool_policy=ToolPolicy(allow=("trace_plan", "trace_capture")),
    )


def test_store_round_trips_a_new_queued_run(tmp_path: Path) -> None:
    # Given a domain-neutral run and an empty durable store
    store = AgentRunStore(tmp_path)

    # When the run is created
    created = store.create(run(), now=10.0)

    # Then its complete context and lifecycle revision are durable
    assert store.get(created.run_id) == created
    assert created.state is AgentRunState.QUEUED
    assert created.created_at == 10.0
    assert created.updated_at == 10.0


def test_store_rejects_a_duplicate_run_identity(tmp_path: Path) -> None:
    # Given the run identity already exists
    store = AgentRunStore(tmp_path)
    _ = store.create(run(), now=10.0)

    # When / Then another create cannot replace the admitted goal
    with pytest.raises(AgentRunAlreadyExistsError):
        _ = store.create(run(), now=11.0)


def test_store_persists_progress_and_review_suspension(tmp_path: Path) -> None:
    # Given a queued run has entered the model/tool loop
    store = AgentRunStore(tmp_path)
    queued = store.create(run(), now=10.0)
    running = store.update(
        queued.run_id,
        AgentRunUpdate(
            expected_revision=queued.revision,
            state=AgentRunState.RUNNING,
            at=11.0,
        ),
    )

    # When a connector observation suspends the run for human review
    waiting = store.update(
        running.run_id,
        AgentRunUpdate(
            expected_revision=running.revision,
            state=AgentRunState.AWAITING_APPROVAL,
            at=12.0,
            history=({"role": "assistant", "content": "artifact ready"},),
            observation=AgentObservation(
                sequence=1,
                kind=ObservationKind.ARTIFACT,
                summary="native artifact verified",
                data={"sha256": "a" * 64},
            ),
        ),
    )

    # Then history, observation, state, and revision commit together
    assert waiting.revision == 3
    assert waiting.state is AgentRunState.AWAITING_APPROVAL
    assert waiting.history[0]["content"] == "artifact ready"
    assert waiting.observations[0].data["sha256"] == "a" * 64
    assert waiting.updated_at == 12.0


def test_store_rejects_an_invalid_transition_and_stale_revision(tmp_path: Path) -> None:
    # Given a queued run
    store = AgentRunStore(tmp_path)
    queued = store.create(run(), now=10.0)

    # When / Then it cannot skip execution and claim completion
    with pytest.raises(AgentRunTransitionError):
        _ = store.update(
            queued.run_id,
            AgentRunUpdate(
                expected_revision=queued.revision,
                state=AgentRunState.COMPLETED,
                terminal_reason="not executed",
                at=11.0,
            ),
        )

    running = store.update(
        queued.run_id,
        AgentRunUpdate(
            expected_revision=queued.revision,
            state=AgentRunState.RUNNING,
            at=12.0,
        ),
    )
    with pytest.raises(AgentRunRevisionError):
        _ = store.update(
            running.run_id,
            AgentRunUpdate(
                expected_revision=queued.revision,
                state=AgentRunState.BLOCKED,
                terminal_reason="context missing",
                at=13.0,
            ),
        )


def test_store_keeps_terminal_runs_immutable(tmp_path: Path) -> None:
    # Given a run has durably completed after execution
    store = AgentRunStore(tmp_path)
    queued = store.create(run(), now=10.0)
    running = store.update(
        queued.run_id,
        AgentRunUpdate(
            expected_revision=queued.revision,
            state=AgentRunState.RUNNING,
            at=11.0,
        ),
    )
    completed = store.update(
        running.run_id,
        AgentRunUpdate(
            expected_revision=running.revision,
            state=AgentRunState.COMPLETED,
            terminal_reason="connector completion validated",
            at=12.0,
        ),
    )

    # When / Then later work cannot reopen the completed run
    with pytest.raises(AgentRunTransitionError):
        _ = store.update(
            completed.run_id,
            AgentRunUpdate(
                expected_revision=completed.revision,
                state=AgentRunState.RUNNING,
                at=13.0,
            ),
        )


def test_store_requeues_rejected_review_with_feedback_and_history_cutoff(tmp_path: Path) -> None:
    # Given a run is suspended with a reviewable artifact
    store = AgentRunStore(tmp_path)
    queued = store.create(run(), now=10.0)
    running = store.update(
        queued.run_id,
        AgentRunUpdate(
            expected_revision=queued.revision,
            state=AgentRunState.RUNNING,
            at=11.0,
        ),
    )
    waiting = store.update(
        running.run_id,
        AgentRunUpdate(
            expected_revision=running.revision,
            state=AgentRunState.AWAITING_APPROVAL,
            at=12.0,
            history=({"type": "function_call_output", "call_id": "call-1", "output": "artifact"},),
        ),
    )

    # When the reviewer rejects the artifact with actionable feedback
    resumed = AgentRunResumer(store).review(
        waiting.run_id,
        AgentReview(
            accepted=False,
            note="카드가 너무 일반적입니다",
            expected_revision=waiting.revision,
            at=13.0,
        ),
    )

    # Then the same run is queued with a durable invalidation boundary
    assert resumed.state is AgentRunState.QUEUED
    assert resumed.observations[-1].kind is ObservationKind.APPROVAL
    assert resumed.observations[-1].data == {
        "accepted": False,
        "history_length": 1,
        "note": "카드가 너무 일반적입니다",
    }


def test_store_completes_an_approved_review_without_another_model_turn(tmp_path: Path) -> None:
    # Given a run is suspended at its final human approval gate
    store = AgentRunStore(tmp_path)
    queued = store.create(run(), now=10.0)
    running = store.update(
        queued.run_id,
        AgentRunUpdate(
            expected_revision=queued.revision,
            state=AgentRunState.RUNNING,
            at=11.0,
        ),
    )
    waiting = store.update(
        running.run_id,
        AgentRunUpdate(
            expected_revision=running.revision,
            state=AgentRunState.AWAITING_APPROVAL,
            at=12.0,
        ),
    )

    # When the reviewer approves the artifact
    completed = AgentRunResumer(store).review(
        waiting.run_id,
        AgentReview(
            accepted=True,
            note=None,
            expected_revision=waiting.revision,
            at=13.0,
        ),
    )

    # Then approval is terminal and does not reopen the model/tool loop
    assert completed.state is AgentRunState.COMPLETED
    assert completed.terminal_reason == "human review approved"
    assert completed.observations[-1].data == {
        "accepted": True,
        "history_length": 0,
        "note": None,
    }


def test_store_requeues_a_run_after_required_user_input_arrives(tmp_path: Path) -> None:
    # Given a running goal has suspended because connector context is incomplete
    store = AgentRunStore(tmp_path)
    queued = store.create(run(), now=10.0)
    running = store.update(
        queued.run_id,
        AgentRunUpdate(
            expected_revision=queued.revision,
            state=AgentRunState.RUNNING,
            at=11.0,
        ),
    )
    waiting = store.update(
        running.run_id,
        AgentRunUpdate(
            expected_revision=running.revision,
            state=AgentRunState.AWAITING_INPUT,
            at=12.0,
        ),
    )

    # When the missing input is supplied
    resumed = AgentRunResumer(store).provide_input(
        waiting.run_id,
        AgentInput(
            expected_revision=waiting.revision,
            text="광고 계정은 KR 대학생 프로필입니다",
            at=13.0,
        ),
    )

    # Then the same goal is queued and the input becomes durable model context
    assert resumed.state is AgentRunState.QUEUED
    assert resumed.observations[-1].kind is ObservationKind.INPUT
    assert resumed.observations[-1].summary == "광고 계정은 KR 대학생 프로필입니다"

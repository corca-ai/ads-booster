from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.runs import (
    AgentGoal,
    AgentRun,
    AgentRunId,
    AgentRunNotFoundError,
    AgentRunState,
    AgentRunStore,
    ToolPolicy,
)
from ads_booster.candidate_generation import CandidateRunConflictError
from ads_booster.candidate_generation.kernel import build_image_review

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class _Decision:
    accepted: bool = True
    note: str | None = None
    at: float = 100.0


def _awaiting_approval(runs: AgentRunStore, run_id: str) -> None:
    run = AgentRun(
        run_id=AgentRunId(run_id),
        connector_id="trace-marketing",
        connector_version="1.0.0",
        goal=AgentGoal(objective="compose one image", success_criteria=("verified export",)),
        tool_policy=ToolPolicy(allow=("trace_generate_marketing_image",)),
    )
    current = runs.create(run, now=1.0)
    while current.state is not AgentRunState.AWAITING_APPROVAL:
        following = _advance(runs, current)
        if following is None:
            break
        current = following


def _advance(runs: AgentRunStore, run: AgentRun) -> AgentRun | None:
    from ads_booster.agent.runs import AgentRunUpdate

    if run.state is AgentRunState.QUEUED:
        return runs.update(
            run.run_id,
            AgentRunUpdate(expected_revision=run.revision, state=AgentRunState.RUNNING, at=2.0),
        )
    if run.state is AgentRunState.RUNNING:
        return runs.update(
            run.run_id,
            AgentRunUpdate(
                expected_revision=run.revision,
                state=AgentRunState.AWAITING_APPROVAL,
                at=3.0,
            ),
        )
    return None


def test_a_decision_reaches_the_run_that_produced_the_image(tmp_path: Path) -> None:
    # Given a run parked at its human approval gate
    runs = AgentRunStore(tmp_path / "core-agent")
    _awaiting_approval(runs, "candidate-1-r1")
    review = build_image_review(runs, at=10.0)

    # When the reviewer approves
    decision = _Decision()
    review.review("candidate-1-r1", accepted=decision.accepted, note=decision.note, at=decision.at)

    # Then the run left its approval gate
    assert runs.get(AgentRunId("candidate-1-r1")).state is not AgentRunState.AWAITING_APPROVAL


def test_every_way_the_run_can_refuse_becomes_one_error_our_layers_know(tmp_path: Path) -> None:
    """The Web layer must never have to catch the execution runtime's error taxonomy.

    A run that is gone and a run in the wrong state are different kernel errors and the
    same fact to a reviewer: the decision did not apply. Translating here is what lets the
    kernel be replaced without touching the router.
    """
    # Given a review adapter over a store with no such run
    runs = AgentRunStore(tmp_path / "core-agent")
    review = build_image_review(runs, at=10.0)

    # When a decision names a run that does not exist
    with pytest.raises(CandidateRunConflictError) as conflict:
        review.review("candidate-missing", accepted=True, note=None, at=11.0)

    # Then it surfaces as our error, with the kernel's own error kept as the cause
    assert isinstance(conflict.value.__cause__, AgentRunNotFoundError)
    assert conflict.value.message


def test_building_the_review_recovers_interrupted_runs(tmp_path: Path) -> None:
    # Given a run left RUNNING by a process that died
    runs = AgentRunStore(tmp_path / "core-agent")
    _ = _advance(
        runs,
        runs.create(
            AgentRun(
                run_id=AgentRunId("candidate-interrupted"),
                connector_id="trace-marketing",
                connector_version="1.0.0",
                goal=AgentGoal(objective="compose", success_criteria=("verified",)),
                tool_policy=ToolPolicy(allow=("trace_generate_marketing_image",)),
            ),
            now=1.0,
        ),
    )
    assert runs.get(AgentRunId("candidate-interrupted")).state is AgentRunState.RUNNING

    # When the adapter is composed
    _ = build_image_review(runs, at=20.0)

    # Then the interrupted run was recovered rather than left mid-flight
    assert runs.get(AgentRunId("candidate-interrupted")).state is not AgentRunState.RUNNING

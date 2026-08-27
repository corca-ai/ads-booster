from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.agent.run_models import (
    AgentInput,
    AgentObservation,
    AgentReview,
    AgentRun,
    AgentRunId,
    AgentRunState,
    AgentRunUpdate,
    ObservationKind,
)

if TYPE_CHECKING:
    from ads_booster.agent.run_store import AgentRunStore


@dataclass(frozen=True, slots=True)
class AgentRunResumer:
    store: AgentRunStore

    def review(self, run_id: AgentRunId, decision: AgentReview) -> AgentRun:
        """Complete or requeue one run at its durable human-review boundary."""
        current = self.store.get(run_id)
        target = AgentRunState.COMPLETED if decision.accepted else AgentRunState.QUEUED
        summary = decision.note or (
            "human review approved" if decision.accepted else "human review rejected"
        )
        return self.store.update(
            run_id,
            AgentRunUpdate(
                expected_revision=decision.expected_revision,
                state=target,
                at=decision.at,
                observation=AgentObservation(
                    sequence=len(current.observations) + 1,
                    kind=ObservationKind.APPROVAL,
                    summary=summary,
                    data={
                        "accepted": decision.accepted,
                        "history_length": len(current.history),
                        "note": decision.note,
                    },
                ),
                terminal_reason="human review approved" if decision.accepted else None,
            ),
        )

    def provide_input(self, run_id: AgentRunId, supplied: AgentInput) -> AgentRun:
        """Requeue one suspended run with durable user-supplied context."""
        current = self.store.get(run_id)
        return self.store.update(
            run_id,
            AgentRunUpdate(
                expected_revision=supplied.expected_revision,
                state=AgentRunState.QUEUED,
                at=supplied.at,
                observation=AgentObservation(
                    sequence=len(current.observations) + 1,
                    kind=ObservationKind.INPUT,
                    summary=supplied.text,
                    data={"history_length": len(current.history)},
                ),
            ),
        )

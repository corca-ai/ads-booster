from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import AgentBudget
from ads_booster.contracts.task_progress import TaskPolicy

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class CompletionPolicy:
    slack_budget: AgentBudget
    segment_policy: TaskPolicy


def load_completion_policy(environment: Mapping[str, str] | None = None) -> CompletionPolicy:
    values = os.environ if environment is None else environment
    return CompletionPolicy(
        slack_budget=AgentBudget(
            max_tool_calls=int(values.get("TRACE_MARKETING_MAX_TOOL_CALLS", "32")),
            max_cost_units=50,
        ),
        segment_policy=TaskPolicy(
            max_decision_calls=int(values.get("TRACE_MARKETING_MAX_DECISION_CALLS", "64"))
        ),
    )


def new_slack_budget(explicit: AgentBudget | None, policy: CompletionPolicy) -> AgentBudget:
    return explicit if explicit is not None else policy.slack_budget

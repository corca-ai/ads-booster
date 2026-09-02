"""Versioned worker capabilities for each hosted marketing reasoning tool."""

from typing import Final

MARKETING_JUDGMENT_CAPABILITIES: Final[dict[str, str]] = {
    "shadow_strategy": "shadow_strategy_v1",
    "market_research": "market_research_v1",
    "creative_plan": "creative_plan_v1",
    "candidate_materialization": "candidate_materialization_v2",
    "experiment_evaluation": "experiment_evaluation_v1",
    "learning_synthesis": "learning_synthesis_v1",
    "outcome_reassessment": "outcome_reassessment_v1",
    "next_experiment": "next_experiment_v1",
}

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ads_booster.agent.runs import (
    AgentGoal,
    AgentRun,
    AgentRunId,
    AgentRunState,
    CompletionDecision,
    CompletionDisposition,
    ConnectorAlreadyRegisteredError,
    ConnectorId,
    ConnectorManifest,
    ConnectorNotFoundError,
    ConnectorRegistry,
    ToolPolicy,
)

if TYPE_CHECKING:
    from ads_booster.tools.models import Tool
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class FakeConnector:
    manifest: ConnectorManifest

    def instructions(self, goal: AgentGoal) -> str:
        return f"connector={self.manifest.connector_id}\ngoal={goal.objective}"

    def context_messages(self, goal: AgentGoal) -> tuple[JsonObject, ...]:
        del goal
        return ()

    def tools(self, goal: AgentGoal) -> tuple[Tool, ...]:
        del goal
        return ()

    def validate_completion(self, run: AgentRun, answer: str) -> CompletionDecision:
        del run
        return CompletionDecision(
            disposition=CompletionDisposition.COMPLETED,
            message=answer,
        )


def connector(name: str) -> FakeConnector:
    return FakeConnector(
        ConnectorManifest(
            connector_id=ConnectorId(name),
            version="1.0.0",
            description=f"{name} domain",
        )
    )


def test_registry_resolves_two_unrelated_domain_connectors() -> None:
    # Given two domain packs that share only the Core connector contract
    trace = connector("trace-marketing")
    other = connector("other-marketing")

    # When both are registered in one immutable registry
    registry = ConnectorRegistry((trace, other))

    # Then callers resolve each connector without a domain-specific branch in Core
    assert registry.get(ConnectorId("trace-marketing"), "1.0.0") is trace
    assert registry.get(ConnectorId("other-marketing"), "1.0.0") is other
    assert registry.ids() == (ConnectorId("trace-marketing"), ConnectorId("other-marketing"))


def test_registry_resolves_exact_versions_of_one_connector() -> None:
    # Given two installed versions of the same connector
    version_one = connector("trace-marketing")
    version_two = FakeConnector(
        ConnectorManifest(
            connector_id=ConnectorId("trace-marketing"),
            version="2.0.0",
            description="trace-marketing v2 domain",
        )
    )

    # When both versions are registered
    registry = ConnectorRegistry((version_one, version_two))

    # Then each durable version resolves to its exact implementation
    assert registry.get(ConnectorId("trace-marketing"), "1.0.0") is version_one
    assert registry.get(ConnectorId("trace-marketing"), "2.0.0") is version_two


def test_registry_rejects_duplicate_connector_ids() -> None:
    # Given two implementations claiming the same connector identity
    first = connector("trace-marketing")
    duplicate = connector("trace-marketing")

    # When / Then registry construction fails before a run can pick one nondeterministically
    with pytest.raises(ConnectorAlreadyRegisteredError) as failure:
        _ = ConnectorRegistry((first, duplicate))
    assert failure.value.connector_id == ConnectorId("trace-marketing")


def test_registry_reports_a_missing_connector_as_a_typed_failure() -> None:
    # Given a registry with no connector for the requested goal
    registry = ConnectorRegistry((connector("trace-marketing"),))

    # When / Then the missing id remains machine-readable
    with pytest.raises(ConnectorNotFoundError) as failure:
        _ = registry.get(ConnectorId("missing-domain"), "1.0.0")
    assert failure.value.connector_id == ConnectorId("missing-domain")


def test_tool_policy_rejects_an_allow_and_deny_overlap() -> None:
    # Given a capability cannot be both executable and forbidden for the same run
    # When / Then the policy is rejected at its input boundary
    with pytest.raises(ValidationError):
        _ = ToolPolicy(allow=("search", "capture"), deny=("capture",))


def test_tool_policy_rejects_provider_invalid_dotted_tool_names() -> None:
    # Given provider function names cannot contain dots
    dotted_name = "trace.capture"

    # When / Then the Agent contract rejects the identifier before provider dispatch
    with pytest.raises(ValidationError):
        _ = ToolPolicy(allow=(dotted_name,))


def test_agent_run_keeps_goal_connector_and_state_domain_neutral() -> None:
    # Given a run whose context contains opaque domain-owned JSON
    goal = AgentGoal(
        objective="Create one reviewable marketing image",
        success_criteria=("native artifact is verified",),
        context={"persona_id": "student", "campaign": {"variation": 2}},
    )

    # When the Agent run is constructed
    run = AgentRun(
        run_id=AgentRunId("run-1"),
        connector_id=ConnectorId("trace-marketing"),
        connector_version="1.0.0",
        goal=goal,
        tool_policy=ToolPolicy(allow=("inspect", "capture")),
    )

    # Then Agent owns lifecycle facts without knowing the context's domain schema
    assert run.state is AgentRunState.QUEUED
    assert run.revision == 1
    assert run.goal.context["persona_id"] == "student"

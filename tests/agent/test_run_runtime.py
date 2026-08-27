from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from ads_booster.agent.runs import (
    AgentGoal,
    AgentRun,
    AgentRunId,
    AgentRunState,
    AgentRunStore,
    AgentRuntime,
    CompletionDecision,
    CompletionDisposition,
    ConnectorId,
    ConnectorManifest,
    ConnectorRegistry,
    ObservationKind,
    SessionBuildRequest,
    ToolPolicy,
)
from ads_booster.agent.session import AgentSession
from ads_booster.contracts.tools import ToolDescriptor
from ads_booster.providers.codex import FunctionCall, ModelTurn
from ads_booster.providers.errors import ProviderError
from ads_booster.tools.models import ToolContext, ToolResult

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.agent.session import ModelClient
    from ads_booster.tools.models import Tool
    from ads_booster.transport.json_types import JsonObject


class EmptyArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True, slots=True)
class RecordingTool:
    name: ClassVar[str] = "trace_capture"
    calls: list[str]

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description="Capture one typed scene",
            parameters=EmptyArgs.model_json_schema(),
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        del arguments, context
        self.calls.append(self.name)
        return ToolResult(ok=True, output="native artifact verified")


@dataclass(frozen=True, slots=True)
class HiddenTool:
    name: ClassVar[str] = "trace_hidden"

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description="Unavailable for this run",
            parameters=EmptyArgs.model_json_schema(),
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        del arguments, context
        return ToolResult(ok=True, output="must not run")


@dataclass(slots=True)  # noqa: MUTABLE_OK
class RecordingModel:
    turns: list[ModelTurn]
    tool_names: list[tuple[str, ...]] = field(default_factory=list)

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        del history
        self.tool_names.append(tuple(tool.name for tool in tools))
        return self.turns.pop(0)


@dataclass(frozen=True, slots=True)
class FailingModel:
    error: ProviderError

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        del history, tools
        raise self.error


@dataclass(slots=True)  # noqa: MUTABLE_OK
class RecordingSessionBuilder:
    model: ModelClient
    tool_context: ToolContext
    requests: list[SessionBuildRequest] = field(default_factory=list)

    def build(self, request: SessionBuildRequest) -> AgentSession:
        self.requests.append(request)
        return AgentSession(
            client=self.model,
            registry=request.registry,
            context=self.tool_context,
            history=list(request.history),
        )


@dataclass(frozen=True, slots=True)
class TraceConnector:
    capture: Tool
    hidden: Tool
    decisions: tuple[CompletionDisposition, ...] = (CompletionDisposition.COMPLETED,)
    manifest: ConnectorManifest = field(
        default_factory=lambda: ConnectorManifest(
            connector_id=ConnectorId("trace-marketing"),
            version="1.0.0",
            description="Trace marketing",
        )
    )

    def instructions(self, goal: AgentGoal) -> str:
        del goal
        return "Use Trace capabilities until a reviewable artifact exists."

    def context_messages(self, goal: AgentGoal) -> tuple[JsonObject, ...]:
        return (
            {
                "role": "developer",
                "content": {"connector": "trace", "persona_id": goal.context["persona_id"]},
            },
        )

    def tools(self, goal: AgentGoal) -> tuple[Tool, ...]:
        del goal
        return (self.capture, self.hidden)

    def validate_completion(self, run: AgentRun, answer: str) -> CompletionDecision:
        model_turns = sum(
            observation.kind is ObservationKind.MODEL for observation in run.observations
        )
        disposition = self.decisions[min(model_turns, len(self.decisions) - 1)]
        return CompletionDecision(disposition=disposition, message=answer)


def create_run(store: AgentRunStore) -> AgentRun:
    return store.create(
        AgentRun(
            run_id=AgentRunId("run-1"),
            connector_id=ConnectorId("trace-marketing"),
            connector_version="1.0.0",
            goal=AgentGoal(
                objective="Create one dynamic marketing image",
                success_criteria=("artifact is ready for review",),
                context={"persona_id": "student"},
            ),
            tool_policy=ToolPolicy(allow=("trace_capture",)),
        ),
        now=10.0,
    )


def test_runtime_executes_connector_tools_and_commits_validated_completion(tmp_path: Path) -> None:
    # Given a queued run, one allowed connector tool, and a model tool call followed by final text
    store = AgentRunStore(tmp_path)
    queued = create_run(store)
    calls: list[str] = []
    connector = TraceConnector(RecordingTool(calls), HiddenTool())
    model = RecordingModel(
        [
            ModelTurn(
                text="",
                calls=(FunctionCall("call-1", "trace_capture", {}),),
            ),
            ModelTurn(text="artifact ready", calls=()),
        ]
    )
    builder = RecordingSessionBuilder(
        model,
        ToolContext(workspace=tmp_path, approval=AllowApproval(), browser_command=()),
    )
    runtime = AgentRuntime(
        store=store,
        connectors=ConnectorRegistry((connector,)),
        sessions=builder,
        clock=IncrementingClock(),
    )

    # When the Core runtime owns the goal execution
    completed = runtime.run(queued.run_id)

    # Then only the allowed connector capability ran and completion became durable
    assert calls == ["trace_capture"]
    assert model.tool_names == [("trace_capture",), ("trace_capture",)]
    assert builder.requests[0].goal.context["persona_id"] == "student"
    assert builder.requests[0].connector_instructions.startswith("Use Trace capabilities")
    assert builder.requests[0].connector_context[0]["role"] == "developer"
    assert completed.state is AgentRunState.COMPLETED
    assert completed.terminal_reason == "artifact ready"
    assert completed.history[-1] == {"role": "assistant", "content": "artifact ready"}
    assert tuple(item.kind for item in completed.observations) == (
        ObservationKind.TOOL,
        ObservationKind.MODEL,
    )
    assert completed.observations[0].data == {
        "call_id": "call-1",
        "error_code": None,
        "ok": True,
        "tool_name": "trace_capture",
    }
    assert completed.observations[-1].summary == "artifact ready"
    assert store.get(queued.run_id) == completed


def test_runtime_replans_when_connector_rejects_the_first_completion(tmp_path: Path) -> None:
    # Given the connector requires one more model turn after the first final answer
    store = AgentRunStore(tmp_path)
    queued = create_run(store)
    connector = TraceConnector(
        RecordingTool([]),
        HiddenTool(),
        decisions=(CompletionDisposition.CONTINUE, CompletionDisposition.COMPLETED),
    )
    model = RecordingModel(
        [
            ModelTurn(text="draft only", calls=()),
            ModelTurn(text="reviewable artifact ready", calls=()),
        ]
    )
    builder = RecordingSessionBuilder(
        model,
        ToolContext(workspace=tmp_path, approval=AllowApproval(), browser_command=()),
    )

    # When connector validation asks the Core loop to continue
    completed = AgentRuntime(
        store=store,
        connectors=ConnectorRegistry((connector,)),
        sessions=builder,
        clock=IncrementingClock(),
    ).run(queued.run_id)

    # Then both model answers are durable observations and only the validated one completes
    assert tuple(item.summary for item in completed.observations) == (
        "draft only",
        "reviewable artifact ready",
    )
    assert completed.state is AgentRunState.COMPLETED
    assert completed.revision == 4


def test_runtime_persists_provider_failure_before_propagating_it(tmp_path: Path) -> None:
    # Given a queued run whose provider request fails
    store = AgentRunStore(tmp_path)
    queued = create_run(store)
    failure = ProviderError("provider_http", "provider rejected the request")
    builder = RecordingSessionBuilder(
        FailingModel(failure),
        ToolContext(workspace=tmp_path, approval=AllowApproval(), browser_command=()),
    )

    # When the Agent runtime executes the failing model turn
    with pytest.raises(ProviderError) as raised:
        _ = AgentRuntime(
            store=store,
            connectors=ConnectorRegistry((TraceConnector(RecordingTool([]), HiddenTool()),)),
            sessions=builder,
            clock=IncrementingClock(),
        ).run(queued.run_id)

    # Then the caller receives the error and the durable run no longer remains running
    failed = store.get(queued.run_id)
    assert raised.value is failure
    assert failed.state is AgentRunState.FAILED
    assert failed.terminal_reason == str(failure)
    assert failed.observations[-1].kind is ObservationKind.FAILURE


@dataclass(frozen=True, slots=True)
class AllowApproval:
    def request(self, action: str, detail: str) -> bool:
        del action, detail
        return True


@dataclass(slots=True)  # noqa: MUTABLE_OK
class IncrementingClock:
    value: float = 10.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value

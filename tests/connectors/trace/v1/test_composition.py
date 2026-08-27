from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.agent.memory import NullMemoryStore
from ads_booster.agent.runs import (
    AgentReview,
    AgentRunId,
    AgentRunResumer,
    AgentRunSessionBuilder,
    AgentRunState,
    AgentRunStore,
)
from ads_booster.config.settings import AgentSettings
from ads_booster.connectors.trace.v1.composition import (
    TraceConnectorApproval,
    TraceV1GenerateOneRunner,
)
from ads_booster.contracts.run import TraceRunState
from ads_booster.providers.codex import FunctionCall, ModelTurn
from ads_booster.providers.errors import ProviderError
from ads_booster.tools.models import ToolContext
from tests.connectors.trace.v1.test_connector import (
    RecordingRunner,
    bundle,
    completed_result,
    plan,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject

_PROVIDER_NETWORK = "provider_network"
_PROVIDER_UNAVAILABLE = "provider unavailable"


@dataclass(slots=True)  # noqa: MUTABLE_OK
class PlannedModel:
    turns: list[ModelTurn]

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        del history
        assert tuple(tool.name for tool in tools) == ("trace_generate_marketing_image",)
        return self.turns.pop(0)


@dataclass(frozen=True, slots=True)
class RunnerFactory:
    runner: RecordingRunner

    def __call__(self, bundle: MarketingContextBundle) -> RecordingRunner:
        del bundle
        return self.runner


@dataclass(frozen=True, slots=True)
class FailingModel:
    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        del history, tools
        raise ProviderError(_PROVIDER_NETWORK, _PROVIDER_UNAVAILABLE)


@dataclass(slots=True)  # noqa: MUTABLE_OK
class IncrementingClock:
    value: float = 10.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


def test_service_runner_executes_a_dynamic_plan_through_the_agent(tmp_path: Path) -> None:
    # Given the service owns a Agent session and a native Trace runner
    model = PlannedModel(
        turns=[
            ModelTurn(
                text="",
                calls=(
                    FunctionCall(
                        call_id="call-1",
                        name="trace_generate_marketing_image",
                        arguments={"plan": plan().model_dump(mode="json")},
                    ),
                ),
            ),
            ModelTurn(text="ready for review", calls=()),
        ]
    )
    trace_runner = RecordingRunner(completed_result())
    store = AgentRunStore(tmp_path / "core-agent")
    service_runner = TraceV1GenerateOneRunner(
        store=store,
        sessions=AgentRunSessionBuilder(
            settings=AgentSettings.from_environment(tmp_path),
            client=model,
            context=ToolContext(tmp_path, TraceConnectorApproval(), ()),
            memory_store=NullMemoryStore(),
        ),
        trace_runners=RunnerFactory(trace_runner),
        clock=IncrementingClock(),
    )

    # When the queue-compatible GenerateOnePort runs the marketing bundle
    result = service_runner.run(bundle())

    # Then the model-authored scene reaches Trace and the durable Core run pauses for review
    assert result.state is TraceRunState.COMPLETED
    assert trace_runner.calls[0][1].wallpaper_plan == plan()
    run = store.get(AgentRunId("dynamic-scene"))
    assert run.state is AgentRunState.AWAITING_APPROVAL
    assert run.observations[-1].summary == "ready for review"


def test_service_runner_records_provider_failure_without_terminating_the_worker(
    tmp_path: Path,
) -> None:
    # Given the provider fails before the model can call a Trace capability
    store = AgentRunStore(tmp_path / "core-agent")
    trace_runner = RecordingRunner(completed_result())
    service_runner = TraceV1GenerateOneRunner(
        store=store,
        sessions=AgentRunSessionBuilder(
            settings=AgentSettings.from_environment(tmp_path),
            client=FailingModel(),
            context=ToolContext(tmp_path, TraceConnectorApproval(), ()),
            memory_store=NullMemoryStore(),
        ),
        trace_runners=RunnerFactory(trace_runner),
        clock=IncrementingClock(),
    )

    # When the queue-compatible runner handles the bundle
    result = service_runner.run(bundle())

    # Then the queue sees a failed Trace result and the durable Core run records the cause
    assert result.state is TraceRunState.FAILED
    assert trace_runner.calls == []
    run = store.get(AgentRunId("dynamic-scene"))
    assert run.state is AgentRunState.FAILED
    assert run.terminal_reason == "provider_network: provider unavailable"


def test_resumed_run_uses_its_admitted_context_snapshot(tmp_path: Path) -> None:
    # Given a completed first attempt is rejected and a caller later supplies drifted context
    model = PlannedModel(
        turns=[
            ModelTurn(
                text="",
                calls=(
                    FunctionCall(
                        call_id="call-1",
                        name="trace_generate_marketing_image",
                        arguments={"plan": plan().model_dump(mode="json")},
                    ),
                ),
            ),
            ModelTurn(text="first artifact", calls=()),
            ModelTurn(
                text="",
                calls=(
                    FunctionCall(
                        call_id="call-2",
                        name="trace_generate_marketing_image",
                        arguments={"plan": plan().model_dump(mode="json")},
                    ),
                ),
            ),
            ModelTurn(text="replacement artifact", calls=()),
        ]
    )
    trace_runner = RecordingRunner(completed_result())
    store = AgentRunStore(tmp_path / "core-agent")
    service_runner = TraceV1GenerateOneRunner(
        store=store,
        sessions=AgentRunSessionBuilder(
            settings=AgentSettings.from_environment(tmp_path),
            client=model,
            context=ToolContext(tmp_path, TraceConnectorApproval(), ()),
            memory_store=NullMemoryStore(),
        ),
        trace_runners=RunnerFactory(trace_runner),
        clock=IncrementingClock(),
    )
    _ = service_runner.run(bundle())
    waiting = store.get(AgentRunId("dynamic-scene"))
    _ = AgentRunResumer(store).review(
        waiting.run_id,
        AgentReview(
            expected_revision=waiting.revision,
            accepted=False,
            note="새 구성이 필요합니다",
            at=100.0,
        ),
    )
    drifted = bundle().model_copy(
        update={"persona": bundle().persona.model_copy(update={"country": "KR"})}
    )

    # When the same durable run is resumed
    _ = service_runner.run(drifted)

    # Then mechanical execution still receives the context admitted with the original goal
    assert len(trace_runner.calls) == 2
    assert trace_runner.calls[1][0] == bundle()

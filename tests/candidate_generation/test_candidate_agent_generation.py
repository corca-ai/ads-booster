from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from ads_booster.agent.runs import (
    AgentRun,
    AgentRunId,
    AgentRunState,
    AgentRunStore,
    AgentRuntime,
    ConnectorRegistry,
    DomainConnector,
    SessionBuildRequest,
)
from ads_booster.agent.session import AgentSession
from ads_booster.auth.codex import OAuthError
from ads_booster.candidate_generation import (
    CandidateAuthRequiredError,
    CandidateContextSource,
    CandidateGenerator,
    CandidateProviderError,
)
from ads_booster.connectors.trace.v1.candidates import TraceCandidateConnector
from ads_booster.providers.codex import FunctionCall, ModelTurn
from ads_booster.providers.errors import ProviderError
from ads_booster.tools.models import ToolContext
from ads_booster.workspace import CandidateSource, CandidateStatus, SqliteWorkspaceStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject
    from ads_booster.workspace import WorkspaceId


class EmptyArgs(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


@dataclass(slots=True)  # noqa: MUTABLE_OK
class CandidateModel:
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
class SessionBuilder:
    model: CandidateModel
    context: ToolContext

    def build(self, request: SessionBuildRequest) -> AgentSession:
        return AgentSession(
            client=self.model,
            registry=request.registry,
            context=self.context,
            history=list(request.history),
            context_prefix=request.connector_context,
        )


@dataclass(frozen=True, slots=True)
class LocalCandidateAgent:
    runs: AgentRunStore
    sessions: SessionBuilder
    clock: Callable[[], float]

    def execute(self, connector: DomainConnector, run: AgentRun) -> AgentRun:
        admitted = self.runs.create(run, now=self.clock())
        return AgentRuntime(
            store=self.runs,
            connectors=ConnectorRegistry((connector,)),
            sessions=self.sessions,
            clock=self.clock,
        ).run(admitted.run_id)


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


def _write_context(root: Path) -> Path:
    directory = root / "context"
    for relative_path in ("core/FACTS.md", "domains/KR/VOICE.md"):
        path = directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(f"# {relative_path}\n팀이 제공한 사실", encoding="utf-8")
    return directory


def _draft(index: int) -> JsonObject:
    return {
        "topic": f"동적 주제 {index}",
        "country": "KR",
        "posting_slot": "morning" if index < 2 else "evening",
        "caption": f"동적 캡션 {index}",
        "hypothesis": f"가설 {index}",
        "refs_used": ["kr-study-day"],
        "principles_applied": [index + 1],
        "appium_prompt": f"서로 다른 촬영 방향 {index}",
        "image_inputs": {
            "trace_items": [f"0{index + 8}:00 일정", f"1{index + 2}:00 후속 일정"],
            "device_time": f"{index + 7:02d}:20",
            "background_intent": f"서로 다른 실제 공간 사진 {index}",
            "language": "ko",
        },
    }


def _workspace(store: SqliteWorkspaceStore) -> WorkspaceId:
    return store.create_workspace("Trace team").workspace.workspace_id


def _generator(
    root: Path,
    store: SqliteWorkspaceStore,
    runs: AgentRunStore,
    model: CandidateModel,
    run_id: str,
) -> CandidateGenerator:
    clock = IncrementingClock()
    return CandidateGenerator(
        store=store,
        context_source=CandidateContextSource(_write_context(root)),
        connector_factory=TraceCandidateConnector,
        agent=LocalCandidateAgent(
            runs,
            SessionBuilder(model, ToolContext(root, AllowApproval(), ())),
            clock,
        ),
        id_factory=lambda: run_id,
    )


def test_candidate_generation_runs_as_an_agent_tool_loop(tmp_path: Path) -> None:
    # Given a workspace, context snapshot, and model-authored batch tool call
    store = SqliteWorkspaceStore(tmp_path)
    runs = AgentRunStore(tmp_path / "core-agent")
    model = CandidateModel(
        [
            ModelTurn(
                text="",
                calls=(
                    FunctionCall(
                        call_id="candidate-call",
                        name="trace_propose_marketing_candidates",
                        arguments={"candidates": [_draft(index) for index in range(4)]},
                    ),
                ),
            ),
            ModelTurn(text="candidate batch stored", calls=()),
        ]
    )
    generator = _generator(tmp_path, store, runs, model, "candidate-batch-run")

    # When automatic candidate generation executes
    created = generator.generate(_workspace(store))

    # Then Core exposes only the typed Trace capability and durably completes the goal
    assert model.tool_names == [
        ("trace_propose_marketing_candidates",),
        ("trace_propose_marketing_candidates",),
    ]
    assert len(created) == 4
    assert all(record.source is CandidateSource.AUTO for record in created)
    assert all(record.status is CandidateStatus.AWAITING_REVIEW for record in created)
    stored_run = runs.get(AgentRunId("candidate-batch-run"))
    assert stored_run.state is AgentRunState.COMPLETED
    assert stored_run.observations[0].data["tool_name"] == "trace_propose_marketing_candidates"


def test_candidate_generation_replans_after_typed_tool_rejection(tmp_path: Path) -> None:
    # Given the first model-authored batch repeats a topic and the second is distinct
    store = SqliteWorkspaceStore(tmp_path)
    runs = AgentRunStore(tmp_path / "core-agent")
    model = CandidateModel(
        [
            ModelTurn(
                text="",
                calls=(
                    FunctionCall(
                        call_id="invalid-batch",
                        name="trace_propose_marketing_candidates",
                        arguments={
                            "candidates": [
                                _draft(0),
                                _draft(0),
                                _draft(2),
                                _draft(3),
                            ]
                        },
                    ),
                ),
            ),
            ModelTurn(
                text="",
                calls=(
                    FunctionCall(
                        call_id="valid-batch",
                        name="trace_propose_marketing_candidates",
                        arguments={"candidates": [_draft(index) for index in range(4)]},
                    ),
                ),
            ),
            ModelTurn(text="corrected batch stored", calls=()),
        ]
    )
    generator = _generator(tmp_path, store, runs, model, "candidate-replan-run")

    # When the Agent executes the goal
    created = generator.generate(_workspace(store))

    # Then typed tool feedback drives replanning without a fixed retry branch
    assert len(created) == 4
    run = runs.get(AgentRunId("candidate-replan-run"))
    assert tuple(observation.data.get("ok") for observation in run.observations[:2]) == (
        False,
        True,
    )


@dataclass(frozen=True, slots=True)
class FailingCandidateAgent:
    error: OAuthError | ProviderError

    def execute(self, connector: DomainConnector, run: AgentRun) -> AgentRun:
        del connector, run
        raise self.error


def test_candidate_generation_preserves_auth_and_provider_failure_boundaries(
    tmp_path: Path,
) -> None:
    # Given Core execution cannot authenticate or reach the provider
    store = SqliteWorkspaceStore(tmp_path)
    context = CandidateContextSource(_write_context(tmp_path))
    workspace_id = _workspace(store)
    auth = CandidateGenerator(
        store=store,
        context_source=context,
        connector_factory=TraceCandidateConnector,
        agent=FailingCandidateAgent(OAuthError("auth_missing", "login required")),
        id_factory=lambda: "candidate-auth-run",
    )
    provider = CandidateGenerator(
        store=store,
        context_source=context,
        connector_factory=TraceCandidateConnector,
        agent=FailingCandidateAgent(ProviderError("provider_network", "unavailable")),
        id_factory=lambda: "candidate-provider-run",
    )

    # When / Then the Web-safe typed failures remain distinct
    with pytest.raises(CandidateAuthRequiredError):
        _ = auth.generate(workspace_id)
    with pytest.raises(CandidateProviderError) as failure:
        _ = provider.generate(workspace_id)
    assert not failure.value.context_overflow
    assert failure.value.provider_code == "provider_network"
    assert "provider_network" in failure.value.message

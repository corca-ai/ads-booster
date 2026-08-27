from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from ads_booster.agent.connector import ConnectorContextError
from ads_booster.agent.run_models import (
    AgentGoal,
    AgentObservation,
    AgentRun,
    AgentRunId,
    AgentRunState,
    AgentRunUpdate,
    CompletionDisposition,
    ObservationKind,
    ToolPolicy,
)
from ads_booster.agent.session import AgentError
from ads_booster.auth.codex import OAuthError
from ads_booster.providers.errors import ProviderError
from ads_booster.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.agent.connector import ConnectorRegistry, DomainConnector
    from ads_booster.agent.run_store import AgentRunStore
    from ads_booster.agent.session import AgentSession, ToolExecutionEvent
    from ads_booster.transport.json_types import JsonObject

_DISPOSITION_STATES: Final = {
    CompletionDisposition.AWAITING_APPROVAL: AgentRunState.AWAITING_APPROVAL,
    CompletionDisposition.AWAITING_INPUT: AgentRunState.AWAITING_INPUT,
    CompletionDisposition.COMPLETED: AgentRunState.COMPLETED,
    CompletionDisposition.FAILED: AgentRunState.FAILED,
    CompletionDisposition.BLOCKED: AgentRunState.BLOCKED,
}
_TERMINAL_DISPOSITIONS: Final = frozenset(
    (
        CompletionDisposition.COMPLETED,
        CompletionDisposition.FAILED,
        CompletionDisposition.BLOCKED,
    )
)
_RUNNABLE_STATES: Final = frozenset((AgentRunState.QUEUED,))


@dataclass(frozen=True, slots=True)
class SessionBuildRequest:
    goal: AgentGoal
    connector_instructions: str
    connector_context: tuple[JsonObject, ...]
    observations: tuple[AgentObservation, ...]
    history: tuple[JsonObject, ...]
    registry: ToolRegistry


class AgentSessionBuilder(Protocol):
    def build(self, request: SessionBuildRequest) -> AgentSession: ...


class AgentRunNotRunnableError(RuntimeError):
    run_id: AgentRunId
    state: AgentRunState

    def __init__(self, run_id: AgentRunId, state: AgentRunState) -> None:
        """Create a failure for a run that cannot resume."""
        self.run_id = run_id
        self.state = state
        super().__init__(run_id, state)


class ToolPolicyResolutionError(RuntimeError):
    missing: tuple[str, ...]

    def __init__(self, missing: tuple[str, ...]) -> None:
        """Create a failure for allowed tools absent from the connector snapshot."""
        self.missing = missing
        super().__init__(*missing)


@dataclass(frozen=True, slots=True)
class AgentRuntime:
    store: AgentRunStore
    connectors: ConnectorRegistry
    sessions: AgentSessionBuilder
    clock: Callable[[], float] = time.time
    deadline_seconds: float = 3_600

    def run(self, run_id: AgentRunId) -> AgentRun:
        """Execute or resume one durable goal until the connector chooses a lifecycle boundary."""
        admitted = self.store.get(run_id)
        if admitted.state not in _RUNNABLE_STATES:
            raise AgentRunNotRunnableError(run_id, admitted.state)
        connector = self.connectors.get(admitted.connector_id, admitted.connector_version)
        registry = self._resolve_tools(connector, admitted.goal, admitted.tool_policy)
        started_at = self.clock()
        current = self.store.update(
            run_id,
            AgentRunUpdate(
                expected_revision=admitted.revision,
                state=AgentRunState.RUNNING,
                at=started_at,
            ),
        )
        try:
            connector_context = connector.context_messages(current.goal)
        except ConnectorContextError as error:
            return self._connector_context_failure(current, error)
        request = SessionBuildRequest(
            goal=current.goal,
            connector_instructions=connector.instructions(current.goal),
            connector_context=connector_context,
            observations=current.observations,
            history=current.history,
            registry=registry,
        )
        session = self.sessions.build(request)
        prompt = current.goal.objective

        def record_tool(event: ToolExecutionEvent) -> None:
            nonlocal current
            current = self.store.update(
                run_id,
                AgentRunUpdate(
                    expected_revision=current.revision,
                    state=AgentRunState.RUNNING,
                    at=self.clock(),
                    history=tuple(session.history),
                    observation=AgentObservation(
                        sequence=len(current.observations) + 1,
                        kind=ObservationKind.TOOL,
                        summary=f"tool {event.name} {'completed' if event.result.ok else 'failed'}",
                        data={
                            "call_id": event.call_id,
                            "error_code": event.result.error_code,
                            "ok": event.result.ok,
                            "tool_name": event.name,
                        },
                    ),
                ),
            )

        while True:
            if self.clock() - started_at > self.deadline_seconds:
                return self._deadline(current, session)
            try:
                answer = session.ask(prompt, on_tool=record_tool)
            except (AgentError, OAuthError, ProviderError) as error:
                _ = self._execution_failure(current, session, error)
                raise
            snapshot = current.model_copy(update={"history": tuple(session.history)})
            decision = connector.validate_completion(snapshot, answer)
            observation = AgentObservation(
                sequence=len(current.observations) + 1,
                kind=ObservationKind.MODEL,
                summary=answer,
                data={
                    "completion_disposition": decision.disposition.value,
                    "history_length": len(session.history),
                },
            )
            target_state = _DISPOSITION_STATES.get(
                decision.disposition,
                AgentRunState.RUNNING,
            )
            current = self.store.update(
                run_id,
                AgentRunUpdate(
                    expected_revision=current.revision,
                    state=target_state,
                    at=self.clock(),
                    history=tuple(session.history),
                    observation=observation,
                    terminal_reason=(
                        decision.message if decision.disposition in _TERMINAL_DISPOSITIONS else None
                    ),
                ),
            )
            if decision.disposition is not CompletionDisposition.CONTINUE:
                return current
            prompt = decision.message

    def _resolve_tools(
        self,
        connector: DomainConnector,
        goal: AgentGoal,
        policy: ToolPolicy,
    ) -> ToolRegistry:
        available = {tool.name: tool for tool in connector.tools(goal)}
        missing = tuple(name for name in policy.allow if name not in available)
        if missing:
            raise ToolPolicyResolutionError(missing)
        return ToolRegistry(
            tuple(available[name] for name in policy.allow if name not in policy.deny)
        )

    def _deadline(self, current: AgentRun, session: AgentSession) -> AgentRun:
        return self.store.update(
            current.run_id,
            AgentRunUpdate(
                expected_revision=current.revision,
                state=AgentRunState.BLOCKED,
                at=self.clock(),
                history=tuple(session.history),
                observation=AgentObservation(
                    sequence=len(current.observations) + 1,
                    kind=ObservationKind.FAILURE,
                    summary="run deadline exceeded",
                ),
                terminal_reason="run deadline exceeded",
            ),
        )

    def _execution_failure(
        self,
        current: AgentRun,
        session: AgentSession,
        error: AgentError | OAuthError | ProviderError,
    ) -> AgentRun:
        message = str(error)
        return self.store.update(
            current.run_id,
            AgentRunUpdate(
                expected_revision=current.revision,
                state=AgentRunState.FAILED,
                at=self.clock(),
                history=tuple(session.history),
                observation=AgentObservation(
                    sequence=len(current.observations) + 1,
                    kind=ObservationKind.FAILURE,
                    summary=message,
                ),
                terminal_reason=message,
            ),
        )

    def _connector_context_failure(
        self,
        current: AgentRun,
        error: ConnectorContextError,
    ) -> AgentRun:
        message = str(error)
        return self.store.update(
            current.run_id,
            AgentRunUpdate(
                expected_revision=current.revision,
                state=AgentRunState.FAILED,
                at=self.clock(),
                observation=AgentObservation(
                    sequence=len(current.observations) + 1,
                    kind=ObservationKind.FAILURE,
                    summary=message,
                ),
                terminal_reason=message,
            ),
        )

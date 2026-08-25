from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol

from trace_capture.agent.control import AgentControl, AgentControlPort
from trace_capture.agent.factory import (
    AgentSessionConfig,
    build_agent_session,
    build_tool_context,
)
from trace_capture.agent.memory import NullMemoryStore
from trace_capture.auth.codex import CodexOAuth
from trace_capture.auth.store import AuthStore
from trace_capture.providers.codex import CodexResponsesClient
from trace_capture.tools.approval import DenyApproval
from trace_capture.tools.registry import default_registry
from trace_capture.transport.http import create_http_client
from trace_capture.web.agent_state import (
    PendingApproval,
    WebAgentStateSnapshot,
    WebAgentStateStore,
)
from trace_capture.web.chat_commands import (
    WebCommandHost,
    WebCommandRequest,
    WebCommandResult,
    dispatch_web_command,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
    from contextlib import AbstractContextManager

    from trace_capture.agent.session import AgentSession, ModelClient
    from trace_capture.config.settings import AgentSettings
    from trace_capture.tools.models import ApprovalPort, ToolContext
    from trace_capture.tools.registry import ToolRegistry
    from trace_capture.transport.json_types import JsonObject
    from trace_capture.workspace import ContextRecord, MemberId, WorkspaceId


@dataclass(frozen=True, slots=True)
class AgentComponents:
    client: ModelClient
    registry: ToolRegistry
    context: ToolContext
    control_factory: AgentControlFactory | None = None


class AgentControlFactory(Protocol):
    def __call__(self, session: AgentSession) -> AgentControlPort: ...


class AgentComponentsFactory(Protocol):
    def open(
        self,
        settings: AgentSettings | None = None,
        approval: ApprovalPort | None = None,
    ) -> AbstractContextManager[AgentComponents]: ...


@dataclass(frozen=True, slots=True)
class ProductionAgentComponents:
    settings: AgentSettings

    @contextmanager
    def open(
        self,
        settings: AgentSettings | None = None,
        approval: ApprovalPort | None = None,
    ) -> Generator[AgentComponents]:
        effective_settings = self.settings if settings is None else settings
        effective_approval = DenyApproval() if approval is None else approval
        with create_http_client() as http:
            auth_store = AuthStore.default()
            oauth = CodexOAuth(http=http, store=auth_store)
            client = CodexResponsesClient(
                http=http,
                oauth=oauth,
                model=effective_settings.model,
            )
            context = build_tool_context(effective_settings, effective_approval, http)

            def control_factory(session: AgentSession) -> AgentControlPort:
                return AgentControl(effective_settings, oauth, client, session)

            yield AgentComponents(client, default_registry(), context, control_factory)


@dataclass(frozen=True, slots=True)
class ChatTurn:
    answer: str
    private_history: tuple[JsonObject, ...]


@dataclass(frozen=True, slots=True)
class WebAgentSessionFactory:
    settings: AgentSettings
    components: AgentComponentsFactory
    state_store: WebAgentStateStore = field(default_factory=WebAgentStateStore)

    @classmethod
    def production(cls, settings: AgentSettings) -> WebAgentSessionFactory:
        return cls(settings=settings, components=ProductionAgentComponents(settings))

    def run(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        history: Sequence[JsonObject],
        contexts: Sequence[ContextRecord],
        prompt: str,
    ) -> ChatTurn:
        state = self.state_store.state(workspace_id, member_id, self.settings.model)
        settings = self._settings(workspace_id, member_id)
        shared_prefix = _shared_context_prefix(contexts)
        with self.components.open(settings, state.approval) as components:
            session = build_agent_session(
                AgentSessionConfig(
                    settings=settings,
                    client=components.client,
                    registry=components.registry,
                    context=components.context,
                    history=(*shared_prefix, *history),
                    memory_store=NullMemoryStore(),
                )
            )
            if components.control_factory is not None:
                _ = components.control_factory(session).set_reasoning(state.reasoning)
            answer = session.ask(prompt)
            return ChatTurn(answer, tuple(session.history[len(shared_prefix) :]))

    def command(self, request: WebCommandRequest) -> WebCommandResult:
        state = self.state_store.state(
            request.workspace_id,
            request.member_id,
            self.settings.model,
        )
        settings = self._settings(request.workspace_id, request.member_id)
        with self.components.open(settings, state.approval) as components:
            session = build_agent_session(
                AgentSessionConfig(
                    settings=settings,
                    client=components.client,
                    registry=components.registry,
                    context=components.context,
                    history=request.history,
                    memory_store=NullMemoryStore(),
                )
            )
            runtime = (
                components.control_factory(session)
                if components.control_factory is not None
                else None
            )
            if runtime is not None:
                _ = runtime.set_reasoning(state.reasoning)
            host = WebCommandHost(
                session=session,
                runtime=runtime,
                state=state,
                state_store=self.state_store,
                store=request.store,
                workspace_id=request.workspace_id,
                member_id=request.member_id,
                session_id=request.session_id,
            )
            _ = dispatch_web_command(host, request.prompt)
            return host.result(self.settings.model)

    def settings_for(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
    ) -> WebAgentStateSnapshot:
        return self.state_store.snapshot(workspace_id, member_id, self.settings.model)

    def pending_approval(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
    ) -> PendingApproval | None:
        state = self.state_store.state(workspace_id, member_id, self.settings.model)
        return state.approval.pending()

    def resolve_approval(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        request_id: str,
        *,
        decision: bool,
    ) -> bool:
        state = self.state_store.state(workspace_id, member_id, self.settings.model)
        return state.approval.resolve(request_id, decision=decision)

    def _settings(self, workspace_id: WorkspaceId, member_id: MemberId) -> AgentSettings:
        snapshot = self.settings_for(workspace_id, member_id)
        return replace(
            self.settings,
            model=snapshot.model,
        )


def _shared_context_prefix(contexts: Sequence[ContextRecord]) -> tuple[JsonObject, ...]:
    if not contexts:
        return ()
    sections = [
        f"[{context.kind.value}:{context.context_id}] {context.title}\n{context.body}"
        for context in contexts
    ]
    content = (
        "Shared workspace context (read-only; never modify these records from chat):\n\n"
        + "\n\n".join(sections)
    )
    return ({"role": "developer", "content": content},)

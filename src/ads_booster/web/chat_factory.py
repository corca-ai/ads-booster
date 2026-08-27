from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol

from ads_booster.agent.control import AgentControl, AgentControlPort
from ads_booster.agent.factory import (
    AgentSessionConfig,
    build_agent_session,
    build_tool_context,
)
from ads_booster.agent.memory import JsonlMemoryStore, NullMemoryStore
from ads_booster.auth.codex import CodexOAuth
from ads_booster.auth.store import AuthStore
from ads_booster.providers.codex import CodexResponsesClient
from ads_booster.tools.approval import DenyApproval
from ads_booster.tools.registry import default_registry
from ads_booster.transport.http import create_http_client
from ads_booster.web.agent_state import (
    PendingApproval,
    WebAgentStateSnapshot,
    WebAgentStateStore,
)
from ads_booster.web.chat_commands import (
    WebCommandHost,
    WebCommandRequest,
    WebCommandResult,
    dispatch_web_command,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from ads_booster.agent.memory import MemoryStore
    from ads_booster.agent.session import AgentSession, ModelClient
    from ads_booster.config.settings import AgentSettings
    from ads_booster.tools.models import ApprovalPort, ToolContext
    from ads_booster.tools.registry import ToolRegistry
    from ads_booster.transport.json_types import JsonObject
    from ads_booster.workspace import ContextRecord, MemberId, WorkspaceId


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
                reasoning_effort=effective_settings.reasoning_effort,
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
    memory_root: Path | None = None

    @classmethod
    def production(
        cls,
        settings: AgentSettings,
        memory_root: Path,
    ) -> WebAgentSessionFactory:
        return cls(
            settings=settings,
            components=ProductionAgentComponents(settings),
            memory_root=memory_root,
        )

    def run(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        history: Sequence[JsonObject],
        contexts: Sequence[ContextRecord],
        prompt: str,
    ) -> ChatTurn:
        state = self.state_store.state(
            workspace_id,
            member_id,
            self.settings.model,
            self.settings.reasoning_effort,
        )
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
                    memory_store=self.memory_store(workspace_id, member_id),
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
            self.settings.reasoning_effort,
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
                    memory_store=self.memory_store(request.workspace_id, request.member_id),
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
        return self.state_store.snapshot(
            workspace_id,
            member_id,
            self.settings.model,
            self.settings.reasoning_effort,
        )

    def pending_approval(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
    ) -> PendingApproval | None:
        state = self.state_store.state(
            workspace_id,
            member_id,
            self.settings.model,
            self.settings.reasoning_effort,
        )
        return state.approval.pending()

    def resolve_approval(
        self,
        workspace_id: WorkspaceId,
        member_id: MemberId,
        request_id: str,
        *,
        decision: bool,
    ) -> bool:
        state = self.state_store.state(
            workspace_id,
            member_id,
            self.settings.model,
            self.settings.reasoning_effort,
        )
        return state.approval.resolve(request_id, decision=decision)

    def _settings(self, workspace_id: WorkspaceId, member_id: MemberId) -> AgentSettings:
        snapshot = self.settings_for(workspace_id, member_id)
        return replace(
            self.settings,
            model=snapshot.model,
        )

    def memory_store(self, workspace_id: WorkspaceId, member_id: MemberId) -> MemoryStore:
        if self.memory_root is None:
            return NullMemoryStore()
        scope = sha256(f"{workspace_id}\0{member_id}".encode()).hexdigest()
        return JsonlMemoryStore(self.memory_root / f"{scope}.jsonl")


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

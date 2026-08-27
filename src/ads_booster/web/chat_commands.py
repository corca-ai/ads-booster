from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

from ads_booster.agent.control import AgentControlError
from ads_booster.agent.tui_approval import PermissionMode
from ads_booster.agent.tui_commands import TuiCommandHost, handle_tui_command
from ads_booster.auth.browser import BrowserOAuthError
from ads_booster.auth.codex import OAuthError
from ads_booster.auth.store import AuthStoreError
from ads_booster.web.schemas import ChatEvent, SessionSummaryResponse
from ads_booster.workspace import (
    PrivateSessionId,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ads_booster.agent.control import AgentControlPort
    from ads_booster.agent.session import AgentSession
    from ads_booster.providers.models import ProviderModel
    from ads_booster.transport.json_types import JsonObject
    from ads_booster.web.agent_state import (
        WebAgentState,
        WebAgentStateSnapshot,
        WebAgentStateStore,
    )
    from ads_booster.workspace import MemberId, WorkspaceId


@dataclass(frozen=True, slots=True)
class WebCommandRequest:
    store: SqliteWorkspaceStore
    workspace_id: WorkspaceId
    member_id: MemberId
    session_id: PrivateSessionId | None
    history: tuple[JsonObject, ...]
    prompt: str


@dataclass(frozen=True, slots=True)
class WebCommandResult:
    session_id: PrivateSessionId | None
    history: tuple[JsonObject, ...]
    revision: int
    replace_history: bool
    events: tuple[ChatEvent, ...]
    sessions: tuple[SessionSummaryResponse, ...]
    models: tuple[ProviderModel, ...]
    settings: WebAgentStateSnapshot


@dataclass(slots=True)  # noqa: MUTABLE_OK
class WebCommandHost(TuiCommandHost):
    session: AgentSession
    runtime: AgentControlPort | None
    state: WebAgentState
    state_store: WebAgentStateStore
    store: SqliteWorkspaceStore
    workspace_id: WorkspaceId
    member_id: MemberId
    session_id: PrivateSessionId | None
    _events: list[ChatEvent] = field(default_factory=list)
    _sessions: tuple[SessionSummaryResponse, ...] = ()
    _models: tuple[ProviderModel, ...] = ()
    _replace_history: bool = False
    oauth_account_id: str | None = None

    @property
    @override
    def busy(self) -> bool:
        return False

    @override
    def new_session(self) -> None:
        self.session_id = None
        self._replace_session(())
        self._replace_history = True
        self.write_system("새 세션을 시작했습니다. 이전 세션은 저장했습니다")

    @override
    def clear_session(self) -> None:
        if self.session_id is not None:
            self.store.delete_private_session(self.workspace_id, self.member_id, self.session_id)
        self.session_id = None
        self._replace_session(())
        self._replace_history = True
        self.write_system("현재 세션을 지우고 새 세션을 시작했습니다")

    @override
    def show_session_picker(self, session_id: str | None = None) -> None:
        if session_id is not None:
            self._resume_session(PrivateSessionId(session_id))
            return
        records = tuple(
            record
            for record in self.store.list_private_sessions(self.workspace_id, self.member_id)
            if record.session_id != self.session_id
        )
        if not records:
            self.write_system("저장된 이전 세션이 없습니다")
            return
        self._sessions = tuple(
            SessionSummaryResponse(
                session_id=record.session_id,
                title=record.title,
                revision=record.revision,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in records
        )
        self.write_system("이전 세션을 선택하세요")

    @override
    def start_oauth(self) -> None:
        if self.runtime is None:
            self.write_error("로그인 기능을 사용할 수 없습니다")
            return
        self.write_system("중앙 에이전트의 OpenAI 로그인 브라우저를 여는 중…")

        def record_auth_url(url: str) -> None:
            self.write_system(f"로그인 URL: {url}")

        try:
            account_id = self.runtime.oauth_login(record_auth_url)
        except (AgentControlError, BrowserOAuthError, OAuthError, AuthStoreError) as error:
            self.write_error(str(error))
            return
        self.oauth_account_id = account_id
        self.write_system("로그인을 완료했습니다")

    @override
    def show_model_picker(self) -> None:
        if self.runtime is None:
            self.write_error("런타임 설정을 사용할 수 없습니다")
            return
        try:
            models = self.runtime.models()
        except AgentControlError as error:
            self.write_error(str(error))
            return
        if not models:
            self.write_error("선택할 수 있는 모델이 없습니다")
            return
        self._models = models
        self.write_system("사용할 모델을 선택하세요")

    @override
    def show_permission_mode(self) -> None:
        label = "매번 확인" if self.state.approval.mode is PermissionMode.ASK else "자동 허용"
        self.write_system(f"승인 방식: {label} ({self.state.approval.mode.value})")

    @override
    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.state.approval.set_mode(mode)
        label = "매번 확인" if mode is PermissionMode.ASK else "자동 허용"
        self.write_system(f"승인 방식이 {label}으로 바뀌었습니다")

    @override
    def write_system(self, message: str) -> None:
        self._events.append(ChatEvent(role="system", content=message))

    @override
    def write_error(self, message: str) -> None:
        self._events.append(ChatEvent(role="error", content=message))

    @override
    def set_status(self, value: str, color: str) -> None:
        _ = (value, color)

    @override
    def refresh_settings(self) -> None:
        if self.runtime is None:
            return
        self.state_store.set_model(self.workspace_id, self.member_id, self.runtime.model())
        self.state_store.set_reasoning(
            self.workspace_id,
            self.member_id,
            self.runtime.reasoning(),
        )

    def result(self, default_model: str) -> WebCommandResult:
        return WebCommandResult(
            session_id=self.session_id,
            history=tuple(self.session.history),
            revision=self._revision(),
            replace_history=self._replace_history,
            events=tuple(self._events),
            sessions=self._sessions,
            models=self._models,
            settings=self.state_store.snapshot(self.workspace_id, self.member_id, default_model),
        )

    def _resume_session(self, session_id: PrivateSessionId) -> None:
        try:
            record = self.store.get_private_session(self.workspace_id, self.member_id, session_id)
        except ScopedRecordNotFoundError:
            self.write_error("저장된 세션을 찾을 수 없습니다")
            return
        self.session_id = record.session_id
        self._replace_session(record.history)
        self._replace_history = True
        self.write_system(f"세션을 다시 열었습니다: {record.title} · {record.session_id}")

    def _replace_session(self, history: Sequence[JsonObject]) -> None:
        self.session = self.session.fork(history)
        if self.runtime is not None:
            self.runtime.set_session(self.session)

    def _revision(self) -> int:
        if self.session_id is None:
            return 0
        return self.store.get_private_session(
            self.workspace_id,
            self.member_id,
            self.session_id,
        ).revision


def dispatch_web_command(host: WebCommandHost, prompt: str) -> bool:
    handled = handle_tui_command(host, prompt)
    if not handled:
        host.write_error(f"알 수 없는 명령어입니다: {prompt} · /help를 확인하세요")
    return handled

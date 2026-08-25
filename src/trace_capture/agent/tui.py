from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final, final, override

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Input, OptionList, RichLog, Static

from trace_capture.agent.context import ContextEvent, ContextPhase
from trace_capture.agent.session_store import (
    NullSessionStore,
    SessionManager,
    SessionStore,
    SessionStoreError,
)
from trace_capture.agent.tui_activity import TuiActivitySink
from trace_capture.agent.tui_commands import handle_tui_command, is_known_command
from trace_capture.agent.tui_context import TuiContextSink
from trace_capture.agent.tui_model import TuiModelCoordinator
from trace_capture.agent.tui_oauth import TuiOAuthCoordinator
from trace_capture.agent.tui_prompt import TuiPromptCoordinator
from trace_capture.agent.tui_session import TuiSessionCoordinator
from trace_capture.agent.tui_state import TuiOperation, TuiState
from trace_capture.agent.tui_styles import TUI_COLORS, TUI_CSS
from trace_capture.agent.tui_view import TuiViewCoordinator

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual.app import AutopilotCallbackType
    from textual.binding import BindingType
    from textual.events import Key

    from trace_capture.agent.control import AgentControlPort
    from trace_capture.agent.session import AgentSession
    from trace_capture.agent.tui_approval import PermissionMode, TuiApproval
    from trace_capture.transport.json_types import JsonObject

_INPUT_ID: Final = "prompt"


@final
class TraceAgentTui(App[None], inherit_bindings=False):
    ENABLE_COMMAND_PALETTE: ClassVar[bool] = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "handle_ctrl_c", "취소 / 종료", priority=True),
        Binding("escape", "cancel_request", "취소", priority=True),
        ("ctrl+l", "clear", "초기화"),
        ("ctrl+k", "focus_input", "입력창"),
    ]
    CSS: ClassVar[str] = TUI_CSS

    def __init__(
        self,
        session: AgentSession,
        approval: TuiApproval,
        runtime: AgentControlPort | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        super().__init__()
        self.session_manager: SessionManager = SessionManager(
            session,
            session_store or NullSessionStore(),
        )
        self.session: AgentSession = self.session_manager.session
        self.approval: TuiApproval = approval
        self.runtime: AgentControlPort | None = runtime
        self.state: TuiState = TuiState()
        self.oauth_account_id: str | None = None
        self.last_response: str | None = None
        self._ctrl_c_pending: bool = False
        self.oauth: TuiOAuthCoordinator = TuiOAuthCoordinator(self)
        self.model_picker: TuiModelCoordinator = TuiModelCoordinator(self)
        self.prompt: TuiPromptCoordinator = TuiPromptCoordinator(self)
        self.sessions: TuiSessionCoordinator = TuiSessionCoordinator(self)
        self.view: TuiViewCoordinator = TuiViewCoordinator(self)
        self.session.context.events = TuiActivitySink(self)
        self.session.context_runtime.observer = TuiContextSink(self)

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="body"), Vertical(id="main-column"):
            yield Static("대화", classes="section-label")
            yield RichLog(id="conversation", wrap=True, markup=False, highlight=False)
            yield Static("준비됨 · Ctrl-C로 취소", id="compact-status")
            yield Static("", id="settings-bar")
            with Vertical(id="oauth-panel"):
                yield Static("로그인", id="oauth-title")
                yield Static("", id="oauth-detail")
            with Vertical(id="approval-panel"):
                yield Static("승인이 필요합니다", id="approval-title")
                yield Static("", id="approval-detail")
                with Horizontal(id="approval-actions"):
                    yield Button("허용", id="approve", variant="success")
                    yield Button("거절", id="deny", variant="error")
            with Vertical(id="model-picker"):
                yield Static("모델 선택", id="model-picker-title")
                yield OptionList(id="model-options")
            with Vertical(id="session-picker"):
                yield Static("세션 선택", id="session-picker-title")
                yield OptionList(id="session-options")
            yield Input(placeholder="무엇을 도와드릴까요?", id=_INPUT_ID)
            yield Static("", id="command-preview")
        yield Footer()

    def on_mount(self) -> None:
        self.approval.bind(self)
        self.write_system("메시지를 입력해 시작하세요. / 로 명령어를 확인합니다.")
        self.write_system("워크스페이스 흐름: 입장 → 준비 → 새 자료 만들기 → 검수")
        self.write_system("예: 캠페인 컨텍스트를 정리해줘")
        self.refresh_settings()
        _ = self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._ctrl_c_pending = False
        if self.busy:
            self.notify("아직 작업 중입니다", severity="warning")
            return
        prompt = event.value.strip()
        completed = self.view.complete_command(prompt)
        if completed is not None:
            event.input.value = completed
            event.input.cursor_position = len(completed)
            return
        event.input.value = ""
        if not prompt:
            return
        if prompt.startswith("/") and not is_known_command(prompt):
            self.write_error(f"알 수 없는 명령어입니다: {prompt} · /help를 확인하세요")
            self.set_status("READY", TUI_COLORS["success"])
            return
        if handle_tui_command(self, prompt):
            return
        self.view.write_user(prompt)
        self.prompt.submit(prompt)

    def on_input_changed(self, event: Input.Changed) -> None:
        self.view.on_input_changed(event.value)

    def on_key(self, event: Key) -> None:
        self.view.on_key(event)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id is None:
            return
        decision = {"approve": True, "deny": False}.get(button_id)
        if decision is None:
            return
        if self.approval.resolve(decision=decision):
            self.view.resolve_approval("approved" if decision else "denied")

    def show_approval(self, action: str, detail: str) -> None:
        self.view.show_approval(action, detail)

    def exit_command(self) -> None:
        _ = self.exit()

    def action_handle_ctrl_c(self) -> None:
        if self._cancel_active_operation():
            self._ctrl_c_pending = False
            return
        if self._ctrl_c_pending:
            self._ctrl_c_pending = False
            self.exit_command()
            return
        self._ctrl_c_pending = True
        self.write_system("한 번 더 누르면 종료합니다")
        _ = self.set_timer(1.0, self._reset_ctrl_c)

    def action_cancel_request(self) -> None:
        self._ctrl_c_pending = False
        _ = self._cancel_active_operation()

    def _reset_ctrl_c(self) -> None:
        self._ctrl_c_pending = False

    def _cancel_active_operation(self) -> bool:
        if self.sessions.cancel():
            return True
        if self.model_picker.cancel():
            return True
        if self.oauth.cancel():
            return True
        return self.prompt.cancel()

    def clear_conversation(self) -> None:
        self.view.clear_conversation()

    def restore_history(self, history: Sequence[JsonObject]) -> None:
        self.view.restore_history(history)

    def activate_session(self) -> None:
        self.session = self.session_manager.session
        self.session.context.events = TuiActivitySink(self)
        self.session.context_runtime.observer = TuiContextSink(self)
        if self.runtime is not None:
            self.runtime.set_session(self.session)

    def new_session(self) -> None:
        self.sessions.new()

    def clear_session(self) -> None:
        self.sessions.clear()

    def show_session_picker(self, session_id: str | None = None) -> None:
        self.sessions.show(session_id)

    def persist_session(self) -> None:
        try:
            self.session_manager.save()
        except SessionStoreError as error:
            self.write_error(str(error))
            self.set_status("ERROR", TUI_COLORS["danger"])

    def refresh_settings(self) -> None:
        self.view.refresh_settings()

    def start_oauth(self) -> None:
        self.oauth.start()

    def show_model_picker(self) -> None:
        self.model_picker.show()

    def show_permission_mode(self) -> None:
        mode = "매번 확인" if self.approval.mode.value == "ask" else "자동 허용"
        self.write_system(f"승인 방식: {mode} ({self.approval.mode.value})")
        self.set_status("READY", TUI_COLORS["success"])

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.approval.set_mode(mode)
        label = "매번 확인" if mode.value == "ask" else "자동 허용"
        self.write_system(f"승인 방식이 {label}으로 바뀌었습니다")
        self.set_status("READY", TUI_COLORS["success"])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "session-options":
            self.sessions.select(event)
            return
        self.model_picker.select(event)

    def approval_timed_out(self) -> None:
        self.view.resolve_approval("timed out")
        self.write_system("승인 시간이 지나 요청을 거절했습니다")
        self.set_status("THINKING", TUI_COLORS["warning"])

    def action_clear(self) -> None:
        if not self.busy:
            _ = handle_tui_command(self, "/clear")

    def action_focus_input(self) -> None:
        _ = self.query_one(Input).focus()

    @override
    def run(
        self,
        *,
        headless: bool = False,
        inline: bool = False,
        inline_no_clear: bool = False,
        mouse: bool = True,
        size: tuple[int, int] | None = None,
        auto_pilot: AutopilotCallbackType | None = None,
    ) -> None:
        return super().run(
            headless=headless,
            inline=inline,
            inline_no_clear=inline_no_clear,
            mouse=mouse,
            size=size,
            auto_pilot=auto_pilot,
        )

    @override
    async def run_async(
        self,
        *,
        headless: bool = False,
        inline: bool = False,
        inline_no_clear: bool = False,
        mouse: bool = True,
        size: tuple[int, int] | None = None,
        auto_pilot: AutopilotCallbackType | None = None,
    ) -> None:
        return await super().run_async(
            headless=headless,
            inline=inline,
            inline_no_clear=inline_no_clear,
            mouse=mouse,
            size=size,
            auto_pilot=auto_pilot,
        )

    def on_unmount(self) -> None:
        self.sessions.shutdown()
        self.model_picker.shutdown()
        self.oauth.shutdown()
        self.prompt.shutdown()
        self.approval.unbind()

    def set_status(self, value: str, color: str) -> None:
        self.state = self.state.with_status(value)
        self.view.set_status(value, color, self.state.detail)

    @property
    def busy(self) -> bool:
        return self.state.busy

    @busy.setter
    def busy(self, value: bool) -> None:
        if value:
            if not self.state.busy:
                self.state = TuiState(TuiOperation.THINKING)
            return
        if self.state.busy:
            self.state = TuiState()

    def write_system(self, message: str) -> None:
        self.view.write_system(message)

    def write_error(self, message: str) -> None:
        self.view.write_error(message)

    def write_assistant(self, message: str) -> None:
        self.view.write_assistant(message)

    def hide_approval(self) -> None:
        self.view.hide_approval()

    def record_tool_started(self, name: str, argument_names: tuple[str, ...]) -> None:
        self.view.record_tool_started(name, argument_names)

    def record_tool_finished(
        self,
        name: str,
        ok: bool,
        error_code: str | None,
        output_length: int,
    ) -> None:
        self.view.record_tool_finished(name, ok, error_code, output_length)

    def record_context_event(self, event: ContextEvent) -> None:
        self.view.record_context_event(event)
        status = {
            ContextPhase.COMPACTION_STARTED: ("COMPACTING CONTEXT", TUI_COLORS["warning"]),
            ContextPhase.FLUSHING_MEMORY: ("FLUSHING MEMORY", TUI_COLORS["warning"]),
            ContextPhase.OVERFLOW_RETRY: ("RECOVERING AFTER OVERFLOW", TUI_COLORS["warning"]),
            ContextPhase.COMPACTION_COMPLETED: ("THINKING", TUI_COLORS["warning"]),
        }
        if event.phase in status:
            self.set_status(*status[event.phase])

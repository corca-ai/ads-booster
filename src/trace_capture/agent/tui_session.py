from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, TypeVar, final, overload

from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from trace_capture.agent.session_store import (
    SessionInfo,
    SessionManager,
    SessionNotFoundError,
    SessionStoreError,
)
from trace_capture.agent.tui_styles import TUI_COLORS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trace_capture.agent.session import AgentSession
    from trace_capture.transport.json_types import JsonObject


_QueryWidget = TypeVar("_QueryWidget", bound=Widget)


class TuiSessionHost(Protocol):
    session: AgentSession
    session_manager: SessionManager

    @property
    def busy(self) -> bool: ...

    @busy.setter
    def busy(self, value: bool) -> None: ...

    @overload
    def query_one(self, selector: str) -> Widget: ...

    @overload
    def query_one(self, selector: type[_QueryWidget]) -> _QueryWidget: ...

    @overload
    def query_one(self, selector: str, expect_type: type[_QueryWidget]) -> _QueryWidget: ...

    def activate_session(self) -> None: ...

    def clear_conversation(self) -> None: ...

    def restore_history(self, history: Sequence[JsonObject]) -> None: ...

    def write_system(self, message: str) -> None: ...

    def write_error(self, message: str) -> None: ...

    def set_status(self, value: str, color: str) -> None: ...


@final
class TuiSessionCoordinator:
    _host: TuiSessionHost
    _picker_open: bool

    def __init__(self, host: TuiSessionHost) -> None:
        """Initialize the session picker for a TUI host."""
        self._host = host
        self._picker_open = False
        self._sessions: tuple[SessionInfo, ...] = ()

    def new(self) -> None:
        try:
            info = self._host.session_manager.new()
        except SessionStoreError as error:
            self._show_error(str(error))
            return
        self._finish_transition(info, "새 세션을 시작했습니다. 이전 세션은 저장했습니다")

    def clear(self) -> None:
        try:
            info = self._host.session_manager.clear()
        except SessionStoreError as error:
            self._show_error(str(error))
            return
        self._finish_transition(info, "현재 세션을 지우고 새 세션을 시작했습니다")

    def show(self, session_id: str | None = None) -> None:
        if session_id is not None:
            self._resume(session_id)
            return
        try:
            sessions = self._host.session_manager.available()
        except SessionStoreError as error:
            self._show_error(str(error))
            return
        if not sessions:
            self._host.write_system("저장된 이전 세션이 없습니다")
            self._host.set_status("READY", TUI_COLORS["success"])
            return
        self._sessions = sessions
        self._picker_open = True
        self._host.busy = True
        self._host.set_status("SELECTING SESSION", TUI_COLORS["info"])
        input_widget = self._host.query_one(Input)
        input_widget.disabled = True
        option_list = self._host.query_one("#session-options", OptionList)
        _ = option_list.clear_options()
        _ = option_list.add_options(tuple(self._option(info) for info in sessions))
        option_list.highlighted = 0
        self._host.query_one("#session-picker", Vertical).styles.display = "block"
        _ = option_list.focus()
        self._host.write_system("↑/↓로 이전 세션을 고른 뒤 Enter를 누르세요")

    def select(self, event: OptionList.OptionSelected) -> None:
        if not self._picker_open:
            return
        option = event.option_list.get_option_at_index(event.option_index)
        session_id = option.id
        if session_id is not None:
            self._resume(session_id)

    def cancel(self) -> bool:
        if not self._picker_open:
            return False
        self._picker_open = False
        self._restore_input()
        self._host.busy = False
        self._hide_picker()
        self._host.write_system("세션 선택을 취소했습니다")
        self._host.set_status("READY", TUI_COLORS["success"])
        return True

    def shutdown(self) -> None:
        if self._picker_open:
            self._picker_open = False
            self._hide_picker()

    def _resume(self, session_id: str) -> None:
        try:
            info = self._host.session_manager.resume(session_id)
        except (SessionNotFoundError, SessionStoreError) as error:
            self._show_error(str(error))
            return
        self._picker_open = False
        self._host.activate_session()
        self._host.clear_conversation()
        self._host.restore_history(self._host.session.history)
        self._restore_input()
        self._hide_picker()
        self._host.busy = False
        self._host.write_system(f"세션을 다시 열었습니다: {info.title} · {info.session_id}")
        self._host.set_status("READY", TUI_COLORS["success"])

    def _finish_transition(self, info: SessionInfo, message: str) -> None:
        self._host.activate_session()
        self._host.clear_conversation()
        self._restore_input()
        self._hide_picker()
        self._host.busy = False
        self._host.write_system(f"{message}: {info.session_id}")
        self._host.set_status("READY", TUI_COLORS["success"])

    def _show_error(self, message: str) -> None:
        self._picker_open = False
        self._restore_input()
        self._hide_picker()
        self._host.busy = False
        self._host.write_error(message)
        self._host.set_status("ERROR", TUI_COLORS["danger"])

    def _option(self, info: SessionInfo) -> Option:
        timestamp = (
            datetime.fromtimestamp(
                info.updated_at,
                tz=UTC,
            )
            .astimezone()
            .strftime("%m-%d %H:%M")
        )
        label = f"{info.title} · {info.message_count} msgs · {timestamp} · {info.session_id}"
        return Option(label, id=info.session_id)

    def _hide_picker(self) -> None:
        self._host.query_one("#session-picker", Vertical).styles.display = "none"

    def _restore_input(self) -> None:
        input_widget = self._host.query_one(Input)
        input_widget.disabled = False
        _ = input_widget.focus()

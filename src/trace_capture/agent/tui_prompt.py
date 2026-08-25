from __future__ import annotations

from functools import partial
from threading import Event
from typing import TYPE_CHECKING, Protocol, TypeVar, overload

from textual.widget import Widget
from textual.widgets import Input

from trace_capture.agent.session import AgentError, AgentSession
from trace_capture.agent.tui_styles import TUI_COLORS
from trace_capture.auth.codex import OAuthError
from trace_capture.providers.errors import ProviderError

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.worker import Worker

    from trace_capture.agent.tui_approval import TuiApproval


_QueryWidget = TypeVar("_QueryWidget", bound=Widget)


class TuiPromptHost(Protocol):
    session: AgentSession
    approval: TuiApproval
    last_response: str | None

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

    def call_from_thread(
        self,
        callback: Callable[..., None],
        *args: str | Event | None,
    ) -> None: ...

    def run_worker(
        self,
        work: Callable[[], None],
        *,
        thread: bool,
        exclusive: bool,
    ) -> Worker[None]: ...

    def set_status(self, value: str, color: str) -> None: ...

    def hide_approval(self) -> None: ...

    def write_system(self, message: str) -> None: ...

    def write_error(self, message: str) -> None: ...

    def write_assistant(self, message: str) -> None: ...

    def persist_session(self) -> None: ...


class TuiPromptCoordinator:
    def __init__(self, host: TuiPromptHost) -> None:
        self._host: TuiPromptHost = host
        self._worker: Worker[None] | None = None
        self._cancel_event: Event | None = None

    def submit(self, prompt: str) -> None:
        host = self._host
        host.busy = True
        host.set_status("THINKING", TUI_COLORS["warning"])
        host.query_one(Input).disabled = True
        cancel_event = Event()
        self._cancel_event = cancel_event
        self._worker = host.run_worker(
            partial(self._run, prompt, cancel_event),
            thread=True,
            exclusive=True,
        )

    def cancel(self) -> bool:
        worker = self._worker
        cancel_event = self._cancel_event
        if worker is None or cancel_event is None or cancel_event.is_set():
            return False
        cancel_event.set()
        worker.cancel()
        self._worker = None
        self._cancel_event = None
        host = self._host
        host.approval.cancel()
        host.busy = False
        host.hide_approval()
        self._restore_input()
        host.last_response = None
        host.write_system("요청을 취소했습니다")
        host.set_status("READY", TUI_COLORS["success"])
        return True

    def shutdown(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._worker is not None:
            self._worker.cancel()
        self._worker = None
        self._cancel_event = None
        self._host.approval.cancel()

    def _run(self, prompt: str, cancel_event: Event) -> None:
        host = self._host
        try:
            response = host.session.ask(prompt)
        except (AgentError, OAuthError, ProviderError) as error:
            if not cancel_event.is_set():
                host.call_from_thread(self._finish, None, str(error), cancel_event)
            return
        if not cancel_event.is_set():
            host.call_from_thread(self._finish, response, None, cancel_event)

    def _finish(
        self,
        response: str | None,
        error: str | None,
        cancel_event: Event,
    ) -> None:
        if cancel_event.is_set() or cancel_event is not self._cancel_event:
            return
        self._worker = None
        self._cancel_event = None
        host = self._host
        host.busy = False
        host.hide_approval()
        self._restore_input()
        if error is not None:
            host.last_response = None
            host.write_error(error)
            host.set_status("ERROR", TUI_COLORS["danger"])
            return
        host.last_response = response or ""
        host.persist_session()
        host.write_assistant(host.last_response)
        host.set_status("READY", TUI_COLORS["success"])

    def _restore_input(self) -> None:
        input_widget = self._host.query_one(Input)
        input_widget.disabled = False
        _ = input_widget.focus()

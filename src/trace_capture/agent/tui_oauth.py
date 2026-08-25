from __future__ import annotations

from functools import partial
from threading import Event
from typing import TYPE_CHECKING, Protocol, TypeVar, overload

from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Input, Static

from trace_capture.agent.control import AgentControlError
from trace_capture.agent.tui_styles import TUI_COLORS
from trace_capture.auth.browser import BrowserOAuthError
from trace_capture.auth.codex import OAuthError
from trace_capture.auth.store import AuthStoreError

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.worker import Worker

    from trace_capture.agent.control import AgentControlPort


_QueryWidget = TypeVar("_QueryWidget", bound=Widget)


class TuiOAuthHost(Protocol):
    runtime: AgentControlPort | None
    oauth_account_id: str | None

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

    def write_error(self, message: str) -> None: ...

    def write_system(self, message: str) -> None: ...


class TuiOAuthCoordinator:
    def __init__(self, host: TuiOAuthHost) -> None:
        self._host: TuiOAuthHost = host
        self._worker: Worker[None] | None = None
        self._cancel_event: Event | None = None

    def start(self) -> None:
        host = self._host
        runtime = host.runtime
        if runtime is None:
            host.write_error("로그인 기능을 사용할 수 없습니다")
            return
        host.busy = True
        host.set_status("AUTHENTICATING", TUI_COLORS["warning"])
        host.query_one(Input).disabled = True
        host.write_system("OpenAI 로그인 브라우저를 여는 중…")
        cancel_event = Event()
        self._cancel_event = cancel_event
        self._worker = host.run_worker(
            partial(self._run_oauth, runtime, cancel_event),
            thread=True,
            exclusive=True,
        )

    def cancel(self) -> bool:
        worker = self._worker
        cancel_event = self._cancel_event
        if worker is None or cancel_event is None:
            return False
        cancel_event.set()
        worker.cancel()
        self._worker = None
        self._cancel_event = None
        host = self._host
        host.busy = False
        self._hide_oauth()
        input_widget = host.query_one(Input)
        input_widget.disabled = False
        _ = input_widget.focus()
        host.write_system("로그인을 취소했습니다")
        host.set_status("READY", TUI_COLORS["success"])
        return True

    def shutdown(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._worker is not None:
            self._worker.cancel()
        self._worker = None
        self._cancel_event = None

    def _run_oauth(
        self,
        runtime: AgentControlPort,
        cancel_event: Event,
    ) -> None:
        host = self._host
        try:
            account_id = runtime.oauth_login(
                partial(self._on_oauth_url, cancel_event),
            )
        except (AgentControlError, BrowserOAuthError, OAuthError, AuthStoreError) as error:
            if not cancel_event.is_set():
                host.call_from_thread(self._finish_oauth, None, str(error), cancel_event)
            return
        if not cancel_event.is_set():
            host.call_from_thread(self._finish_oauth, account_id, None, cancel_event)

    def _on_oauth_url(self, cancel_event: Event, url: str) -> None:
        self._host.call_from_thread(self._show_oauth_url, cancel_event, url)

    def _show_oauth_url(self, cancel_event: Event, url: str) -> None:
        if cancel_event.is_set() or cancel_event is not self._cancel_event:
            return
        host = self._host
        host.query_one("#oauth-detail", Static).update(f"브라우저를 열었습니다.\n{url}")
        host.query_one("#oauth-panel", Vertical).styles.display = "block"

    def _finish_oauth(
        self,
        account_id: str | None,
        error: str | None,
        cancel_event: Event,
    ) -> None:
        if cancel_event.is_set() or cancel_event is not self._cancel_event:
            return
        self._worker = None
        self._cancel_event = None
        host = self._host
        host.busy = False
        self._hide_oauth()
        input_widget = host.query_one(Input)
        input_widget.disabled = False
        _ = input_widget.focus()
        if error is not None:
            host.write_error(error)
            host.set_status("ERROR", TUI_COLORS["danger"])
            return
        host.oauth_account_id = account_id
        host.write_system("로그인을 완료했습니다")
        host.set_status("READY", TUI_COLORS["success"])

    def _hide_oauth(self) -> None:
        self._host.query_one("#oauth-panel", Vertical).styles.display = "none"

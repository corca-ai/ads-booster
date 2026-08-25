from __future__ import annotations

import threading
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class TuiAppPort(Protocol):
    def call_from_thread(
        self,
        callback: Callable[..., None],
        *args: str,
    ) -> None: ...

    def show_approval(self, action: str, detail: str) -> None: ...

    def approval_timed_out(self) -> None: ...


class PermissionMode(StrEnum):
    ASK = "ask"
    YOLO = "yolo"


class TuiApproval:
    _app: TuiAppPort | None
    _lock: threading.Lock
    _pending: tuple[str, str, threading.Event] | None
    _decision: bool
    _mode: PermissionMode
    timeout_seconds: float

    def __init__(
        self,
        timeout_seconds: float = 300.0,
        mode: PermissionMode = PermissionMode.YOLO,
    ) -> None:
        self._app = None
        self._lock = threading.Lock()
        self._pending = None
        self._decision = False
        self._mode = mode
        self.timeout_seconds = timeout_seconds

    @property
    def mode(self) -> PermissionMode:
        with self._lock:
            return self._mode

    def set_mode(self, mode: PermissionMode) -> None:
        with self._lock:
            self._mode = mode

    def bind(self, app: TuiAppPort) -> None:
        self._app = app

    def unbind(self) -> None:
        self._app = None

    def request(self, action: str, detail: str) -> bool:
        event = threading.Event()
        app = self._app
        with self._lock:
            if self._mode is PermissionMode.YOLO:
                return True
            self._pending = (action, detail, event)
            self._decision = False
        if app is None:
            return False
        app.call_from_thread(app.show_approval, action, detail)
        if not event.wait(self.timeout_seconds) and self.resolve(decision=False):
            app.call_from_thread(app.approval_timed_out)
        with self._lock:
            return self._decision

    def resolve(self, *, decision: bool) -> bool:
        with self._lock:
            pending = self._pending
            if pending is None:
                return False
            self._decision = decision
            self._pending = None
        pending[2].set()
        return True

    def cancel(self) -> None:
        _ = self.resolve(decision=False)

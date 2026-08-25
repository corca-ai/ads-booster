from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, final

if TYPE_CHECKING:
    from collections.abc import Callable

    from trace_capture.agent.context import ContextEvent


class TuiContextHost(Protocol):
    def call_from_thread(self, callback: Callable[..., None], *args: ContextEvent) -> None: ...

    def record_context_event(self, event: ContextEvent) -> None: ...


@final
class TuiContextSink:
    _host: TuiContextHost

    def __init__(self, host: TuiContextHost) -> None:
        self._host = host

    def on_context_event(self, event: ContextEvent) -> None:
        self._host.call_from_thread(self._host.record_context_event, event)

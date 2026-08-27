from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar, overload

from textual.widget import Widget

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.transport.json_types import JsonObject


_QueryWidget = TypeVar("_QueryWidget", bound=Widget)


class TuiActivityHost(Protocol):
    @overload
    def query_one(self, selector: str) -> Widget: ...

    @overload
    def query_one(self, selector: type[_QueryWidget]) -> _QueryWidget: ...

    @overload
    def query_one(self, selector: str, expect_type: type[_QueryWidget]) -> _QueryWidget: ...

    def call_from_thread(
        self,
        callback: Callable[..., None],
        *args: str | bool | int | tuple[str, ...] | None,
    ) -> None: ...

    def record_tool_started(self, name: str, argument_names: tuple[str, ...]) -> None: ...

    def record_tool_finished(
        self,
        name: str,
        ok: bool,
        error_code: str | None,
        output_length: int,
    ) -> None: ...


class TuiActivitySink:
    def __init__(self, host: TuiActivityHost) -> None:
        """Forward worker-thread tool events to the Textual event loop."""
        self._host: TuiActivityHost = host

    def tool_started(self, name: str, arguments: JsonObject) -> None:
        self._host.call_from_thread(
            self._host.record_tool_started,
            name,
            tuple(sorted(arguments)),
        )

    def tool_finished(
        self,
        name: str,
        ok: bool,
        error_code: str | None,
        output_length: int,
    ) -> None:
        self._host.call_from_thread(
            self._host.record_tool_finished,
            name,
            ok,
            error_code,
            output_length,
        )

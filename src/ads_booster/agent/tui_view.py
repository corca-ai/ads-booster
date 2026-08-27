from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeVar, overload

from rich.box import ROUNDED
from rich.panel import Panel
from rich.text import Text
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Button, Input, RichLog, Static

from ads_booster.agent.control import AgentControlError
from ads_booster.agent.tui_commands import (
    command_completion,
    command_preview,
    command_suggestions,
)
from ads_booster.agent.tui_styles import TUI_COLORS, TUI_STATUS_LABELS
from ads_booster.auth.store import AuthStoreError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from textual.events import Key

    from ads_booster.agent.context import ContextEvent
    from ads_booster.agent.control import AgentControlPort
    from ads_booster.transport.json_types import JsonObject


_QueryWidget = TypeVar("_QueryWidget", bound=Widget)


class TuiViewHost(Protocol):
    runtime: AgentControlPort | None

    @overload
    def query_one(self, selector: str) -> Widget: ...

    @overload
    def query_one(self, selector: type[_QueryWidget]) -> _QueryWidget: ...

    @overload
    def query_one(self, selector: str, expect_type: type[_QueryWidget]) -> _QueryWidget: ...


class TuiViewCoordinator:
    def __init__(self, host: TuiViewHost) -> None:
        self._host: TuiViewHost = host
        self._command_suggestions: tuple[str, ...] = ()
        self._command_index: int = 0

    def complete_command(self, value: str) -> str | None:
        return command_completion(value, self._command_index)

    def on_input_changed(self, value: str) -> None:
        suggestions = command_suggestions(value)
        if suggestions != self._command_suggestions:
            self._command_index = 0
        self._command_suggestions = suggestions
        self._render_command_preview(value)

    def on_key(self, event: Key) -> None:
        if not self._host.query_one(Input).has_focus:
            return
        if self._command_suggestions:
            actions: dict[str, Callable[[], None]] = {
                "up": lambda: self._move_command_selection(-1),
                "down": lambda: self._move_command_selection(1),
                "tab": self._complete_command,
            }
            action = actions.get(event.key)
            if action is not None:
                _ = event.stop()
                _ = event.prevent_default()
                action()
                return
        self._scroll_conversation(event)

    def _scroll_conversation(self, event: Key) -> None:
        conversation = self._host.query_one("#conversation", RichLog)
        actions: dict[str, Callable[[], None]] = {
            "up": lambda: conversation.scroll_up(animate=False),
            "down": lambda: conversation.scroll_down(animate=False),
            "pageup": lambda: conversation.scroll_page_up(animate=False),
            "pagedown": lambda: conversation.scroll_page_down(animate=False),
        }
        action = actions.get(event.key)
        if action is None:
            return
        _ = event.stop()
        _ = event.prevent_default()
        action()

    def refresh_settings(self) -> None:
        settings = self._host.query_one("#settings-bar", Static)
        runtime = self._host.runtime
        if runtime is None:
            settings.update("설정 · 런타임 제어를 사용할 수 없습니다")
            return
        try:
            auth_status = runtime.auth_status()
        except (AgentControlError, AuthStoreError):  # fmt: skip
            auth_status = "unavailable"
        auth_label = auth_status.split(" · ", 1)[0].casefold()
        auth_label = {
            "not logged in": "로그인 필요",
            "logged in": "로그인됨",
            "authenticated": "인증됨",
        }.get(auth_label, auth_label)
        workspace_name = Path(runtime.workspace()).name or "현재 폴더"
        reasoning = runtime.reasoning() or "default"
        reasoning = {"default": "기본", "low": "낮음", "medium": "중간", "high": "높음"}.get(
            reasoning,
            reasoning,
        )
        settings_parts = (
            f"모델 {runtime.model()}",
            f"추론 {reasoning}",
            f"인증 {auth_label}",
            f"작업공간 {workspace_name}",
        )
        settings.update(" · ".join(settings_parts))

    def show_approval(self, action: str, detail: str) -> None:
        self._host.query_one("#approval-title", Static).update(f"승인이 필요합니다 · {action}")
        self._host.query_one("#approval-detail", Static).update(detail)
        self._host.query_one("#approval-panel", Vertical).styles.display = "block"
        _ = self._host.query_one("#approve", Button).focus()
        self.set_status("WAITING FOR APPROVAL", TUI_COLORS["warning"])

    def hide_approval(self) -> None:
        self._host.query_one("#approval-panel", Vertical).styles.display = "none"

    def clear_conversation(self) -> None:
        conversation = self._host.query_one("#conversation", RichLog)
        _ = conversation.clear()
        conversation.scroll_home(animate=False)

    def restore_history(self, history: Sequence[JsonObject]) -> None:
        for entry in history:
            role = entry.get("role")
            content = entry.get("content")
            if not isinstance(content, str):
                continue
            if role == "user":
                self.write_user(content)
            if role == "assistant":
                self.write_assistant(content)

    def set_status(self, value: str, color: str, detail: str = "") -> None:
        label = TUI_STATUS_LABELS.get(value, value)
        status_line = f"{label} · Ctrl-C로 취소"
        if detail:
            status_line = f"{label} · {detail} · Ctrl-C로 취소"
        status = self._host.query_one("#compact-status", Static)
        status.update(status_line)
        status.styles.color = color

    def write_system(self, message: str) -> None:
        _ = self._host.query_one("#conversation", RichLog).write(
            Text(f"  {message}", style=TUI_COLORS["text-muted"])
        )

    def write_user(self, message: str) -> None:
        self._write_message("나", message, TUI_COLORS["info"], TUI_COLORS["text"])

    def write_assistant(self, message: str) -> None:
        self._write_message("Trace", message, TUI_COLORS["accent"], TUI_COLORS["text"])

    def write_error(self, message: str) -> None:
        self._write_message("오류", message, TUI_COLORS["danger"], TUI_COLORS["text"])

    def record_tool_started(self, name: str, argument_names: tuple[str, ...]) -> None:
        arguments = ", ".join(argument_names) if argument_names else "no arguments"
        _ = self._host.query_one("#conversation", RichLog).write(
            Text.assemble(
                ("RUN  ", f"bold {TUI_COLORS['warning']}"),
                (name, f"bold {TUI_COLORS['text']}"),
                (f"  ({arguments})", TUI_COLORS["text-quiet"]),
            )
        )

    def record_tool_finished(
        self,
        name: str,
        ok: bool,
        error_code: str | None,
        output_length: int,
    ) -> None:
        marker = "OK   " if ok else "FAIL "
        color = TUI_COLORS["success"] if ok else TUI_COLORS["danger"]
        suffix = f"{output_length:,} chars"
        if error_code is not None:
            suffix = f"{error_code} · {suffix}"
        _ = self._host.query_one("#conversation", RichLog).write(
            Text.assemble(
                (marker, f"bold {color}"),
                (name, TUI_COLORS["text"]),
                (f"  {suffix}", TUI_COLORS["text-muted"]),
            )
        )

    def record_approval(self, decision: str) -> None:
        _ = self._host.query_one("#conversation", RichLog).write(
            Text(f"승인  {decision}", style=TUI_COLORS["warning"])
        )

    def record_context_event(self, event: ContextEvent) -> None:
        usage = event.usage
        detail = ""
        if usage is not None:
            detail = f" · est={usage.estimated_input_tokens} tokens"
        if event.metadata is not None:
            cache = event.metadata.cache
            cached_tokens = (
                str(cache.cached_input_tokens)
                if cache.cached_input_tokens is not None
                else "unknown"
            )
            cache_detail = f" · cached={cached_tokens} · prefix={cache.prefix_digest[:8]}"
            detail += cache_detail
        _ = self._host.query_one("#conversation", RichLog).write(
            Text(f"컨텍스트  {event.phase.value}{detail}", style=TUI_COLORS["info"])
        )

    def resolve_approval(self, decision: str) -> None:
        self.hide_approval()
        self.record_approval(decision)
        self.set_status("THINKING", TUI_COLORS["warning"], "허용한 작업을 실행하는 중")

    def _write_message(self, title: str, message: str, title_color: str, body_color: str) -> None:
        renderable = Panel(
            Text(message, style=body_color),
            title=title,
            title_align="left",
            border_style=title_color,
            box=ROUNDED,
            padding=(0, 1),
        )
        conversation = self._host.query_one("#conversation", RichLog)
        _ = conversation.write(renderable, width=conversation.scrollable_content_region.width)

    def _render_command_preview(self, value: str) -> None:
        preview = self._host.query_one("#command-preview", Static)
        content = command_preview(value, self._command_index)
        preview.update(content or "")
        preview.styles.display = "block" if content is not None else "none"

    def _move_command_selection(self, offset: int) -> None:
        self._command_index = (self._command_index + offset) % len(self._command_suggestions)
        self._render_command_preview(self._host.query_one(Input).value)

    def _complete_command(self) -> None:
        input_widget = self._host.query_one(Input)
        completed = command_completion(input_widget.value, self._command_index)
        if completed is None:
            return
        input_widget.value = completed
        input_widget.cursor_position = len(completed)

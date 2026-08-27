from __future__ import annotations

from functools import partial
from threading import Event
from typing import TYPE_CHECKING, Protocol, TypeVar, overload

from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ads_booster.agent.control import AgentControlError
from ads_booster.agent.tui_reasoning import reasoning_index, reasoning_options
from ads_booster.agent.tui_styles import TUI_COLORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.worker import Worker

    from ads_booster.agent.control import AgentControlPort
    from ads_booster.providers.models import ProviderModel, ProviderReasoningLevel


_QueryWidget = TypeVar("_QueryWidget", bound=Widget)


class TuiModelHost(Protocol):
    runtime: AgentControlPort | None

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
        *args: str | tuple[ProviderModel, ...] | Event | None,
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

    def refresh_settings(self) -> None: ...


class TuiModelCoordinator:
    def __init__(self, host: TuiModelHost) -> None:
        self._host: TuiModelHost = host
        self._worker: Worker[None] | None = None
        self._cancel_event: Event | None = None
        self._picker_open: bool = False
        self._models: tuple[ProviderModel, ...] = ()
        self._reasoning_options: tuple[ProviderReasoningLevel, ...] = ()

    def show(self) -> None:
        host = self._host
        runtime = host.runtime
        if runtime is None:
            host.write_error("런타임 설정을 사용할 수 없습니다")
            return
        host.busy = True
        host.set_status("LOADING MODELS", TUI_COLORS["warning"])
        host.query_one(Input).disabled = True
        cancel_event = Event()
        self._cancel_event = cancel_event
        self._reasoning_options = ()
        self._worker = host.run_worker(
            partial(self._load_models, runtime, cancel_event),
            thread=True,
            exclusive=True,
        )

    def cancel(self) -> bool:
        worker = self._worker
        cancel_event = self._cancel_event
        if worker is not None and cancel_event is not None:
            cancel_event.set()
            worker.cancel()
            self._worker = None
            self._cancel_event = None
            self._picker_open = False
            self._reasoning_options = ()
            self._host.busy = False
            self._hide_picker()
            self._restore_input()
            self._host.write_system("모델 선택을 취소했습니다")
            self._host.set_status("READY", TUI_COLORS["success"])
            return True
        if not self._picker_open:
            return False
        self._picker_open = False
        self._reasoning_options = ()
        self._hide_picker()
        self._restore_input()
        self._host.write_system("모델 선택을 취소했습니다")
        self._host.busy = False
        self._host.set_status("READY", TUI_COLORS["success"])
        return True

    def shutdown(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._worker is not None:
            self._worker.cancel()
        self._worker = None
        self._cancel_event = None
        self._picker_open = False
        self._reasoning_options = ()

    def select(self, event: OptionList.OptionSelected) -> None:
        if not self._picker_open:
            return
        option = event.option_list.get_option_at_index(event.option_index)
        model_id = option.id
        if model_id is None:
            return
        if self._reasoning_options:
            self._select_reasoning(model_id)
            return
        self._select_model(model_id)

    def _select_model(self, model_id: str) -> None:
        runtime = self._host.runtime
        if runtime is None:
            _ = self.cancel()
            self._host.write_error("런타임 설정을 사용할 수 없습니다")
            return
        selected_model = next((model for model in self._models if model.slug == model_id), None)
        try:
            model = runtime.set_model(model_id)
        except AgentControlError as error:
            self._host.write_error(str(error))
            self._host.set_status("ERROR", TUI_COLORS["danger"])
            return
        if selected_model is not None and selected_model.supported_reasoning_levels:
            self._reasoning_options = selected_model.supported_reasoning_levels
            self._show_reasoning_picker(selected_model)
            return
        _ = runtime.set_reasoning(None)
        self._finish_selection(f"모델을 {model}(으)로 설정했습니다")

    def _select_reasoning(self, effort: str) -> None:
        runtime = self._host.runtime
        if runtime is None:
            _ = self.cancel()
            self._host.write_error("런타임 설정을 사용할 수 없습니다")
            return
        try:
            selected_effort = runtime.set_reasoning(effort)
        except AgentControlError as error:
            self._host.write_error(str(error))
            self._host.set_status("ERROR", TUI_COLORS["danger"])
            return
        label = selected_effort or "default"
        self._finish_selection(f"모델을 {runtime.model()}(으)로 설정했습니다 · 추론 강도 {label}")

    def _show_reasoning_picker(self, model: ProviderModel) -> None:
        runtime = self._host.runtime
        if runtime is None:
            return
        options = reasoning_options(self._reasoning_options)
        option_list = self._host.query_one("#model-options", OptionList)
        _ = option_list.clear_options()
        _ = option_list.add_options(options)
        current_effort = runtime.reasoning()
        current_index = reasoning_index(
            self._reasoning_options,
            current_effort,
            model.default_reasoning_level,
        )
        option_list.highlighted = current_index
        self._host.query_one("#model-picker-title", Static).update(
            f"추론 강도 선택 · 모델: {model.display_name}"
        )
        _ = option_list.focus()
        self._host.write_system("↑/↓로 추론 강도를 고른 뒤 Enter를 누르세요")

    def _finish_selection(self, message: str) -> None:
        self._picker_open = False
        self._reasoning_options = ()
        self._hide_picker()
        self._restore_input()
        self._host.write_system(message)
        self._host.set_status("READY", TUI_COLORS["success"])
        self._host.refresh_settings()

    def _load_models(self, runtime: AgentControlPort, cancel_event: Event) -> None:
        host = self._host
        try:
            models = runtime.models()
        except AgentControlError as error:
            if not cancel_event.is_set():
                host.call_from_thread(self._finish_loading, (), str(error), cancel_event)
            return
        if not cancel_event.is_set():
            host.call_from_thread(self._finish_loading, models, None, cancel_event)

    def _finish_loading(
        self,
        models: tuple[ProviderModel, ...],
        error: str | None,
        cancel_event: Event,
    ) -> None:
        if cancel_event.is_set() or cancel_event is not self._cancel_event:
            return
        self._worker = None
        self._cancel_event = None
        host = self._host
        host.busy = False
        if error is not None:
            self._restore_input()
            host.write_error(error)
            host.set_status("ERROR", TUI_COLORS["danger"])
            return
        if not models:
            self._restore_input()
            host.write_error("선택할 수 있는 모델이 없습니다")
            host.set_status("ERROR", TUI_COLORS["danger"])
            return
        self._models = models
        self._reasoning_options = ()
        current_model = host.runtime.model() if host.runtime is not None else ""
        option_values = (
            Option(f"{model.display_name} ({model.slug})", id=model.slug) for model in models
        )
        options = tuple(option_values)
        option_list = host.query_one("#model-options", OptionList)
        _ = option_list.clear_options()
        _ = option_list.add_options(options)
        current_index = next(
            (index for index, model in enumerate(models) if model.slug == current_model),
            0,
        )
        option_list.highlighted = current_index
        host.query_one("#model-picker-title", Static).update(f"모델 선택 · 현재: {current_model}")
        host.query_one("#model-picker", Vertical).styles.display = "block"
        self._picker_open = True
        _ = option_list.focus()
        host.query_one(Input).disabled = True
        host.write_system("↑/↓로 모델을 고른 뒤 Enter를 누르세요")
        host.set_status("READY", TUI_COLORS["success"])

    def _hide_picker(self) -> None:
        self._host.query_one("#model-picker", Vertical).styles.display = "none"

    def _restore_input(self) -> None:
        input_widget = self._host.query_one(Input)
        input_widget.disabled = False
        _ = input_widget.focus()

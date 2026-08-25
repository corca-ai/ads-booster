from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING, final

import pytest
from textual.widgets import Header, Input, OptionList, RichLog, Static

from trace_capture.agent.session import AgentSession
from trace_capture.agent.tui import TraceAgentTui
from trace_capture.agent.tui_approval import TuiApproval
from trace_capture.providers.codex import ModelTurn
from trace_capture.providers.models import ProviderModel
from trace_capture.tools.approval import DenyApproval
from trace_capture.tools.models import ToolContext
from trace_capture.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from trace_capture.contracts.tools import ToolDescriptor
    from trace_capture.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class ImmediateModel:
    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        _ = (history, tools)
        return ModelTurn("pong", ())


class BlockingModel:
    started: Event
    release: Event

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        _ = (history, tools)
        self.started.set()
        _ = self.release.wait(timeout=5)
        return ModelTurn("late response", ())


@final
class ImmediateRuntime:
    model_value: str
    workspace_value: str
    oauth_account: str
    logged_out: bool

    def __init__(
        self,
        model_value: str = "gpt-5.5",
        workspace_value: str = "workspace",
        oauth_account: str = "account",
        logged_out: bool = False,
    ) -> None:
        self.model_value = model_value
        self.reasoning_value: str | None = None
        self.workspace_value = workspace_value
        self.oauth_account = oauth_account
        self.logged_out = logged_out
        self.oauth_providers: list[str | None] = []

    model_catalog: tuple[ProviderModel, ...] = (
        ProviderModel(slug="gpt-5.5", display_name="GPT-5.5", description="Frontier"),
        ProviderModel(slug="gpt-5.4", display_name="GPT-5.4", description="Balanced"),
    )

    def __call__(self, on_auth: Callable[[str], None]) -> str:
        on_auth("https://auth.example.test/oauth")
        return self.oauth_account

    def oauth_login(
        self,
        on_auth: Callable[[str], None],
        provider: str | None = None,
    ) -> str:
        self.oauth_providers.append(provider)
        return self(on_auth)

    def set_session(self, session: AgentSession) -> None:
        _ = session

    def auth_status(self) -> str:
        return "logged in"

    def auth_logout(self, provider: str | None = None) -> None:
        _ = provider
        self.logged_out = True

    def model(self) -> str:
        return self.model_value

    def models(self) -> tuple[ProviderModel, ...]:
        return self.model_catalog

    def reasoning(self) -> str | None:
        return self.reasoning_value

    def workspace(self) -> str:
        return self.workspace_value

    def set_model(self, value: str) -> str:
        self.model_value = value.strip()
        return self.model_value

    def set_reasoning(self, value: str | None) -> str | None:
        self.reasoning_value = value.strip() if value is not None else None
        return self.reasoning_value

    def set_workspace(self, value: str) -> str:
        self.workspace_value = value.strip()
        return self.workspace_value


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_tui_renders_shell_and_completes_prompt(tmp_path: Path) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    app = TraceAgentTui(session=session, approval=approval)

    async with app.run_test() as pilot:
        app.query_one(Input).value = "야"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert isinstance(app.query_one("#conversation", RichLog), RichLog)
        assert app.last_response == "pong"


@pytest.mark.anyio
async def test_tui_previews_commands_while_slash_is_typed(tmp_path: Path) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    app = TraceAgentTui(session=session, approval=approval)

    async with app.run_test() as pilot:
        assert len(app.query(Header)) == 0
        app.query_one(Input).value = "/"
        await pilot.pause()

        preview = app.query_one("#command-preview", Static)
        assert preview.styles.display == "block"
        assert "/oauth" not in str(preview.renderable)
        assert "/auth" in str(preview.renderable)
        assert all(
            command not in str(preview.renderable)
            for command in ("/quit", "/exit", "/settings", "/context", "/status", "/workspace")
        )

        app.query_one(Input).value = "/auth"
        await pilot.pause()
        assert "/auth login" in str(preview.renderable)

        app.query_one(Input).value = "hello"
        await pilot.pause()
        assert preview.styles.display == "none"


@pytest.mark.anyio
async def test_tui_slash_preview_supports_arrow_selection_and_tab_completion(
    tmp_path: Path,
) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    app = TraceAgentTui(session=session, approval=approval)

    async with app.run_test() as pilot:
        input_widget = app.query_one(Input)
        input_widget.value = "/"
        await pilot.pause()
        await pilot.press("down")

        preview = app.query_one("#command-preview", Static)
        assert "> /model" in str(preview.renderable)

        await pilot.press("tab")
        assert input_widget.value == "/model "


@pytest.mark.anyio
async def test_tui_auth_login_command_runs_login_without_model_prompt(tmp_path: Path) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    runtime = ImmediateRuntime()
    app = TraceAgentTui(
        session=session,
        approval=approval,
        runtime=runtime,
    )

    async with app.run_test() as pilot:
        app.query_one(Input).value = "/auth login"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert app.oauth_account_id == "account"
        assert runtime.oauth_providers == [None]
        assert app.last_response is None
        assert "준비됨" in str(app.query_one("#compact-status", Static).renderable)


@pytest.mark.anyio
async def test_tui_model_command_updates_runtime_and_workspace_is_retired(tmp_path: Path) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    runtime = ImmediateRuntime()
    app = TraceAgentTui(session=session, approval=approval, runtime=runtime)

    async with app.run_test() as pilot:
        app.query_one(Input).value = "/model gpt-test"
        await pilot.press("enter")
        app.query_one(Input).value = f"/workspace {tmp_path}"
        await pilot.press("enter")

        assert runtime.model_value == "gpt-test"
        assert runtime.workspace_value == "workspace"


@pytest.mark.anyio
async def test_tui_model_command_shows_provider_models_and_applies_selection(
    tmp_path: Path,
) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    runtime = ImmediateRuntime()
    app = TraceAgentTui(session=session, approval=approval, runtime=runtime)

    async with app.run_test() as pilot:
        app.query_one(Input).value = "/model"
        await pilot.press("enter")
        await pilot.pause(0.2)

        options = app.query_one("#model-options", OptionList)
        assert options.option_count == 2
        assert options.get_option_at_index(0).id == "gpt-5.5"
        assert options.get_option_at_index(1).id == "gpt-5.4"

        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert runtime.model_value == "gpt-5.4"
        assert app.query_one("#model-picker").styles.display == "none"


@pytest.mark.anyio
async def test_tui_auth_logout_command_calls_runtime(tmp_path: Path) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    runtime = ImmediateRuntime()
    app = TraceAgentTui(session=session, approval=approval, runtime=runtime)

    async with app.run_test() as pilot:
        app.query_one(Input).value = "/auth logout"
        await pilot.press("enter")

        assert runtime.logged_out


@pytest.mark.anyio
async def test_tui_exits_after_two_ctrl_c_presses(tmp_path: Path) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    app = TraceAgentTui(session=session, approval=approval)

    async with app.run_test() as pilot:
        await pilot.press("ctrl+c")
        assert app.is_running
        await pilot.press("ctrl+c")
        await pilot.pause()

        assert not app.is_running


@pytest.mark.anyio
@pytest.mark.parametrize("cancel_key", ["escape", "ctrl+c"])
async def test_tui_cancels_in_flight_prompt_on_cancel_key(
    tmp_path: Path,
    cancel_key: str,
) -> None:
    model = BlockingModel()
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(model, ToolRegistry(()), context)
    app = TraceAgentTui(session=session, approval=approval)

    try:
        async with app.run_test() as pilot:
            app.query_one(Input).value = "slow request"
            await pilot.press("enter")
            assert model.started.wait(timeout=2)

            await pilot.press(cancel_key)
            await pilot.pause()

            assert not app.busy
            assert app.last_response is None
            model.release.set()
            await pilot.pause(0.2)
            assert app.last_response is None
    finally:
        model.release.set()


@pytest.mark.anyio
async def test_tui_does_not_exit_on_ctrl_q(tmp_path: Path) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    app = TraceAgentTui(session=session, approval=approval)

    async with app.run_test() as pilot:
        await pilot.press("ctrl+q")
        await pilot.pause()

        assert app.is_running
        status_text = str(app.query_one("#compact-status", Static).renderable)
        assert "Ctrl-Q" not in status_text
        assert "Ctrl-C로 취소" in status_text


def test_tui_run_defaults_mouse_enabled(tmp_path: Path) -> None:
    _ = tmp_path
    run_defaults = TraceAgentTui.run.__kwdefaults__
    run_async_defaults = TraceAgentTui.run_async.__kwdefaults__

    assert isinstance(run_defaults, dict)
    assert isinstance(run_async_defaults, dict)
    assert run_defaults.get("mouse") is True
    assert run_async_defaults.get("mouse") is True

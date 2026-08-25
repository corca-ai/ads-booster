from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest
from textual.widgets import Input, Static

from trace_capture.agent.session import AgentSession
from trace_capture.agent.tui import TraceAgentTui
from trace_capture.agent.tui_approval import PermissionMode, TuiApproval
from trace_capture.agent.tui_commands import (
    command_completion,
    command_preview,
    command_suggestions,
    handle_tui_command,
    is_known_command,
)
from trace_capture.providers.codex import ModelTurn
from trace_capture.tools.approval import DenyApproval
from trace_capture.tools.models import ToolContext
from trace_capture.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from pathlib import Path

    from trace_capture.agent.control import AgentControlPort


@dataclass(frozen=True, slots=True)
class ImmediateModel:
    def respond(self, *args: object, **kwargs: object) -> ModelTurn:
        _ = (args, kwargs)
        return ModelTurn("pong", ())


class RecordingCommandHost:
    started_oauth: list[str | None]
    system_messages: list[str]
    error_messages: list[str]
    session: AgentSession
    runtime: AgentControlPort | None
    oauth_account_id: str | None
    busy: bool
    new_session_count: int
    clear_session_count: int
    session_requests: list[str | None]
    permission_modes: list[PermissionMode]

    def __init__(self) -> None:
        self.started_oauth = []
        self.system_messages = []
        self.error_messages = []
        self.session = cast("AgentSession", cast("object", None))
        self.runtime = None
        self.oauth_account_id = None
        self.busy = False
        self.new_session_count = 0
        self.clear_session_count = 0
        self.session_requests = []
        self.permission_modes = []

    def clear_conversation(self) -> None:
        pass

    def new_session(self) -> None:
        self.new_session_count += 1

    def clear_session(self) -> None:
        self.clear_session_count += 1

    def show_session_picker(self, session_id: str | None = None) -> None:
        self.session_requests.append(session_id)

    def start_oauth(self, provider: str | None = None) -> None:
        self.started_oauth.append(provider)

    def show_model_picker(self) -> None:
        pass

    def show_permission_mode(self) -> None:
        self.system_messages.append("Permission mode: yolo")

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.permission_modes.append(mode)

    def write_system(self, message: str) -> None:
        self.system_messages.append(message)

    def write_error(self, message: str) -> None:
        self.error_messages.append(message)

    def set_status(self, value: str, color: str) -> None:
        _ = (value, color)

    def refresh_settings(self) -> None:
        pass


def test_command_suggestions_for_root_and_auth() -> None:
    # When user enters '/'
    root_suggs = command_suggestions("/")
    assert root_suggs == (
        "/auth",
        "/model",
        "/permission",
        "/new",
        "/clear",
        "/session",
        "/help",
    )

    # When user enters '/auth'
    auth_suggs = command_suggestions("/auth")
    assert "/auth login" in auth_suggs
    assert "/auth status" in auth_suggs
    assert "/auth logout" in auth_suggs

    permission_suggs = command_suggestions("/permission")
    assert permission_suggs == ("/permission ask", "/permission yolo")


def test_command_preview_renders_strictly_vertical_list() -> None:
    preview = command_preview("/auth", selected_index=1)
    assert preview is not None
    lines = preview.splitlines()
    assert "명령어" in lines[0]

    # Verify each subsequent line corresponds to exactly one command (vertical layout)
    command_lines = lines[1:]
    assert len(command_lines) >= 3
    # Check marker is on selected index (selected_index=1 -> second command)
    assert ">" in command_lines[1]
    assert ">" not in command_lines[0]
    assert ">" not in command_lines[2]


def test_command_preview_windows_scroll_when_exceeding_max_items() -> None:
    preview = command_preview("/", selected_index=6)
    assert preview is not None
    assert "(1/" not in preview
    assert "> /help" in preview
    assert all(
        command not in preview
        for command in ("/quit", "/exit", "/settings", "/context", "/status", "/workspace")
    )


def test_command_completion() -> None:
    assert command_completion("/mod", 0) == "/model "
    assert command_completion("/work", 0) is None
    assert command_completion("/auth l", 0) == "/auth login"
    assert command_completion("/permission", 0) is None


def test_is_known_command() -> None:
    assert is_known_command("/auth login") is True
    assert is_known_command("/model gpt-5.5") is True
    assert is_known_command("/auth login openai") is False
    assert is_known_command("/auth logout openai") is False
    assert is_known_command("/quit") is False
    assert is_known_command("/exit") is False
    assert is_known_command("/workspace") is False
    assert is_known_command("/status") is False
    assert is_known_command("/context") is False
    assert is_known_command("/settings") is False
    assert is_known_command("/invalid") is False
    assert is_known_command("/new") is True
    assert is_known_command("/session") is True
    assert is_known_command("/session abc123") is True
    assert is_known_command("/session ") is True
    assert is_known_command("/permission") is True
    assert is_known_command("/permission ask") is True
    assert is_known_command("/permission yolo") is True
    assert is_known_command("/permission always") is False


def test_handle_tui_command_routes_session_lifecycle() -> None:
    host = RecordingCommandHost()

    assert handle_tui_command(host, "/new") is True
    assert handle_tui_command(host, "/clear") is True
    assert handle_tui_command(host, "/session") is True
    assert handle_tui_command(host, "/session abc123") is True

    assert host.new_session_count == 1
    assert host.clear_session_count == 1
    assert host.session_requests == [None, "abc123"]

    assert handle_tui_command(host, "/permission ask") is True
    assert handle_tui_command(host, "/permission yolo") is True
    assert host.permission_modes == [PermissionMode.ASK, PermissionMode.YOLO]


def test_handle_tui_command_rejects_provider_suffix() -> None:
    host = RecordingCommandHost()
    assert handle_tui_command(host, "/auth login") is True
    assert host.started_oauth == [None]

    assert handle_tui_command(host, "/auth login openai") is False
    assert host.started_oauth == [None]


def test_help_hides_retired_commands() -> None:
    host = RecordingCommandHost()

    assert handle_tui_command(host, "/help") is True
    help_text = " ".join(host.system_messages).casefold()

    assert all(
        command not in help_text
        for command in ("/quit", "/exit", "/settings", "/context", "/status", "/workspace")
    )
    assert "/permission [ask|yolo]" in help_text


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True, slots=True)
class MockControlRuntime:
    def set_session(self, session: AgentSession) -> None:
        _ = session

    def oauth_login(
        self,
        on_auth: object | None = None,
        provider: str | None = None,
    ) -> str:
        _ = (on_auth, provider)
        return "mock_user"

    def auth_status(self) -> str:
        return "not logged in"

    def auth_logout(self, provider: str | None = None) -> None:
        _ = provider

    def model(self) -> str:
        return "gpt-5.5"

    def models(self) -> tuple[object, ...]:
        return ()

    def reasoning(self) -> str | None:
        return None

    def workspace(self) -> str:
        return "."

    def set_model(self, value: str) -> str:
        return value

    def set_reasoning(self, value: str | None) -> str | None:
        return value

    def set_workspace(self, value: str) -> str:
        return value


@pytest.mark.anyio
async def test_tui_auth_login_skips_provider_picker(tmp_path: Path) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    runtime = cast("AgentControlPort", cast("object", MockControlRuntime()))
    app = TraceAgentTui(session=session, approval=approval, runtime=runtime)

    async with app.run_test() as pilot:
        input_widget = app.query_one(Input)
        input_widget.value = "/auth login"
        await pilot.press("enter")
        await pilot.pause(0.2)

        picker = app.query_one("#model-picker")
        assert picker.styles.display == "none"
        assert "준비됨" in str(app.query_one("#compact-status", Static).renderable)


def test_auth_prefix_command_completion() -> None:
    # /auth is a prefix command: it should complete to selected sub-command
    assert command_completion("/auth", 0) == "/auth login"
    assert command_completion("/auth", 1) == "/auth status"
    assert command_completion("/auth", 2) == "/auth logout"
    # Fully-typed leaf commands don't complete
    assert command_completion("/auth login", 0) is None
    assert command_completion("/auth status", 0) is None
    assert command_completion("/auth logout", 0) is None


@pytest.mark.anyio
async def test_tui_auth_arrow_navigation_and_enter_completion(tmp_path: Path) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    runtime = cast("AgentControlPort", cast("object", MockControlRuntime()))
    app = TraceAgentTui(session=session, approval=approval, runtime=runtime)

    async with app.run_test() as pilot:
        input_widget = app.query_one(Input)
        input_widget.value = "/auth"
        await pilot.pause(0.05)

        preview = app.query_one("#command-preview", Static)
        assert "> /auth login" in str(preview.renderable)

        # Press down arrow to move selection to /auth status
        await pilot.press("down")
        await pilot.pause(0.05)
        assert "> /auth status" in str(preview.renderable)

        # Press Enter: fills /auth status into the input
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert input_widget.value == "/auth status"


@pytest.mark.anyio
async def test_tui_auth_default_enter_completes_to_login(tmp_path: Path) -> None:
    approval = TuiApproval()
    context = ToolContext(tmp_path, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    runtime = cast("AgentControlPort", cast("object", MockControlRuntime()))
    app = TraceAgentTui(session=session, approval=approval, runtime=runtime)

    async with app.run_test() as pilot:
        input_widget = app.query_one(Input)
        input_widget.value = "/auth"
        await pilot.pause(0.05)

        # Press Enter without moving selection -> completes to default (/auth login)
        await pilot.press("enter")
        await pilot.pause(0.05)
        assert input_widget.value == "/auth login"

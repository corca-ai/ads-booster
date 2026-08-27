from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from textual.widgets import Input, OptionList, RichLog

from ads_booster.agent.repl import Repl
from ads_booster.agent.session import AgentSession
from ads_booster.agent.session_store import (
    JsonSessionStore,
    SessionManager,
    SessionNotFoundError,
)
from ads_booster.agent.tui import TraceAgentTui
from ads_booster.agent.tui_approval import TuiApproval
from ads_booster.providers.codex import ModelTurn
from ads_booster.tools.approval import DenyApproval
from ads_booster.tools.models import ToolContext
from ads_booster.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class ImmediateModel:
    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        _ = (history, tools)
        return ModelTurn("pong", ())


class ScriptedInput:
    store: JsonSessionStore
    step: int

    def __init__(self, store: JsonSessionStore) -> None:
        self.store = store
        self.step = 0

    def __call__(self, prompt: str) -> str:
        _ = prompt
        if self.step < 2:
            value = ("save this plain session", "/new")[self.step]
            self.step += 1
            return value
        if self.step != 2:
            raise EOFError
        session_id = self.store.list_sessions()[0].session_id
        value = f"/session {session_id}"
        self.step += 1
        return value


def make_manager(tmp_path: Path) -> SessionManager:
    session = AgentSession(
        ImmediateModel(),
        ToolRegistry(()),
        ToolContext(tmp_path, DenyApproval(), ()),
    )
    return SessionManager(session, JsonSessionStore(tmp_path / "sessions"), session_id="active")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_session_manager_persists_history_when_starting_new_session(tmp_path: Path) -> None:
    # Given an active session with a completed conversation
    manager = make_manager(tmp_path)
    manager.session.history.extend(
        [
            {"role": "user", "content": "JP calendar launch"},
            {"role": "assistant", "content": "Drafted the launch scene."},
        ]
    )

    # When the user starts a new session without clearing the old one
    previous_id = manager.session_id
    _ = manager.new()

    # Then the old history remains resumable and the active session is empty
    stored = manager.store.load(previous_id)
    assert stored is not None
    assert stored.history[0]["content"] == "JP calendar launch"
    assert manager.session.history == []
    assert manager.session_id != previous_id


def test_session_manager_clear_deletes_active_session_before_starting_new(
    tmp_path: Path,
) -> None:
    # Given an active session that has been persisted
    manager = make_manager(tmp_path)
    manager.session.history.append({"role": "user", "content": "delete this"})
    manager.save()
    previous_id = manager.session_id

    # When the user clears the active session
    _ = manager.clear()

    # Then its durable copy is gone and a fresh session is active
    assert manager.store.load(previous_id) is None
    assert manager.session.history == []
    assert manager.session_id != previous_id


def test_session_manager_resumes_a_previous_session(tmp_path: Path) -> None:
    # Given a previous session saved by /new
    manager = make_manager(tmp_path)
    manager.session.history.extend(
        [
            {"role": "user", "content": "restore this brief"},
            {"role": "assistant", "content": "restored answer"},
        ]
    )
    previous_id = manager.session_id
    _ = manager.new()

    # When the user selects that session from the session list
    _ = manager.resume(previous_id)

    # Then the model-facing history is restored exactly
    assert manager.session_id == previous_id
    assert [entry["content"] for entry in manager.session.history] == [
        "restore this brief",
        "restored answer",
    ]


def test_session_manager_saves_unsaved_active_history_before_resume(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    manager.session.history.append({"role": "user", "content": "first session"})
    previous_id = manager.session_id
    _ = manager.new()
    unsaved_id = manager.session_id
    manager.session.history.append({"role": "user", "content": "unsaved session"})

    _ = manager.resume(previous_id)

    stored = manager.store.load(unsaved_id)
    assert stored is not None
    assert stored.history[0]["content"] == "unsaved session"


def test_session_manager_rejects_unknown_session_id(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)

    with pytest.raises(SessionNotFoundError):
        _ = manager.resume("missing")


def test_plain_repl_lists_and_resumes_persisted_sessions(tmp_path: Path) -> None:
    store = JsonSessionStore(tmp_path / "sessions")
    manager = make_manager(tmp_path)
    output: list[str] = []
    Repl(
        session=manager.session,
        input_fn=ScriptedInput(store),
        output_fn=output.append,
        session_store=store,
    ).run()

    assert any(line.startswith("Resumed session ") for line in output)
    assert len(store.list_sessions()) == 1


@pytest.mark.anyio
async def test_tui_new_session_lists_and_resumes_previous_history(tmp_path: Path) -> None:
    # Given a TUI backed by a durable session directory
    store = JsonSessionStore(tmp_path / "sessions")
    manager = make_manager(tmp_path)
    app = TraceAgentTui(
        session=manager.session,
        approval=TuiApproval(),
        session_store=store,
    )

    # When a conversation is saved, a new session is started, and the old one is selected
    async with app.run_test() as pilot:
        app.query_one(Input).value = "restore this UI conversation"
        await pilot.press("enter")
        await pilot.pause(0.2)
        previous_id = app.session_manager.session_id

        app.query_one(Input).value = "/new"
        await pilot.press("enter")
        await pilot.pause()
        assert app.session.history == []
        assert store.load(previous_id) is not None

        app.query_one(Input).value = "/session"
        await pilot.press("enter")
        await pilot.pause()
        options = app.query_one("#session-options", OptionList)
        assert options.get_option_at_index(0).id == previous_id

        await pilot.press("enter")
        await pilot.pause()

        # Then the selected session becomes active with its original model history
        assert app.session_manager.session_id == previous_id
        assert "restore this UI conversation" in str(app.query_one("#conversation", RichLog).lines)
        conversation = app.query_one("#conversation", RichLog)
        assert conversation.max_scroll_x == 0
        assert conversation.scroll_offset.y == 0


@pytest.mark.anyio
async def test_tui_clear_deletes_active_session_and_starts_fresh_history(tmp_path: Path) -> None:
    # Given a TUI that has saved its active session
    store = JsonSessionStore(tmp_path / "sessions")
    manager = make_manager(tmp_path)
    app = TraceAgentTui(
        session=manager.session,
        approval=TuiApproval(),
        session_store=store,
    )

    # When a conversation is saved and the active session is cleared
    async with app.run_test() as pilot:
        app.query_one(Input).value = "remove me"
        await pilot.press("enter")
        await pilot.pause(0.2)
        previous_id = app.session_manager.session_id
        assert store.load(previous_id) is not None

        app.query_one(Input).value = "/clear"
        await pilot.press("enter")
        await pilot.pause()

        # Then the old durable session is deleted and the new active history is empty
        assert store.load(previous_id) is None
        assert app.session_manager.session_id != previous_id
        assert app.session.history == []

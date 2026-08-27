from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread
from typing import TYPE_CHECKING, final

import pytest
from textual.widgets import Input, RichLog

from ads_booster.agent.context import (
    ContextEvent,
    ContextPhase,
    ContextTrigger,
    ContextUsage,
)
from ads_booster.agent.session import AgentSession
from ads_booster.agent.tui import TraceAgentTui
from ads_booster.agent.tui_approval import PermissionMode, TuiApproval
from ads_booster.providers.codex import FunctionCall, ModelTurn
from ads_booster.providers.models import (
    ProviderCacheMetrics,
    ProviderModel,
    ProviderResponseMetadata,
)
from ads_booster.tools.filesystem import FileWriteTool
from ads_booster.tools.models import ToolContext
from ads_booster.tools.registry import ToolRegistry
from tests.agent.test_agent_tui import ImmediateModel, ImmediateRuntime
from tests.agent.test_agent_tui_redesign import RecordingModel

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class StubModel:
    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        _ = history, tools
        return ModelTurn("ok", ())


@final
class LateCallbackRuntime:
    started: Event
    release: Event
    callback: Callable[[str], None]
    model_value: str
    reasoning_value: str | None

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.callback = lambda _url: None
        self.model_value = "gpt-test"
        self.reasoning_value = None

    def oauth_login(
        self,
        on_auth: Callable[[str], None],
        provider: str | None = None,
    ) -> str | None:
        _ = provider
        self.callback = on_auth
        self.started.set()
        _ = self.release.wait(timeout=5)
        return "account"

    def set_session(self, session: AgentSession) -> None:
        _ = session

    def auth_status(self) -> str:
        return "not logged in"

    def auth_logout(self, provider: str | None = None) -> None:
        _ = provider

    def model(self) -> str:
        return self.model_value

    def models(self) -> tuple[ProviderModel, ...]:
        return ()

    def reasoning(self) -> str | None:
        return self.reasoning_value

    def workspace(self) -> str:
        return "."

    def set_model(self, value: str) -> str:
        self.model_value = value
        return value

    def set_reasoning(self, value: str | None) -> str | None:
        self.reasoning_value = value
        return value

    def set_workspace(self, value: str) -> str:
        return value


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_tui_activity_keeps_unreported_cache_unknown(tmp_path: Path) -> None:
    approval = TuiApproval()
    session = AgentSession(StubModel(), ToolRegistry(()), ToolContext(tmp_path, approval, ()))
    app = TraceAgentTui(session=session, approval=approval)
    event = ContextEvent(
        phase=ContextPhase.PROVIDER_RESPONSE,
        trigger=ContextTrigger.TURN,
        usage=ContextUsage(
            estimated_input_tokens=10,
            soft_limit=100,
            hard_limit=120,
            pruned_tool_outputs=0,
            projection_version=1,
        ),
        metadata=ProviderResponseMetadata(
            cache=ProviderCacheMetrics(prefix_digest="stable-prefix"),
        ),
    )

    async with app.run_test() as pilot:
        app.record_context_event(event)
        await pilot.pause()

        assert "cached=unknown" in str(app.query_one("#conversation", RichLog).lines)


@pytest.mark.anyio
async def test_tui_ignores_oauth_url_after_cancel(tmp_path: Path) -> None:
    approval = TuiApproval()
    runtime = LateCallbackRuntime()
    session = AgentSession(ImmediateModel(), ToolRegistry(()), ToolContext(tmp_path, approval, ()))
    app = TraceAgentTui(session=session, approval=approval, runtime=runtime)

    try:
        async with app.run_test() as pilot:
            app.query_one(Input).value = "/auth login"
            await pilot.press("enter")
            assert runtime.started.wait(timeout=2)

            await pilot.press("escape")
            callback_thread = Thread(
                target=runtime.callback,
                args=("https://auth.example.test/late",),
            )
            callback_thread.start()
            callback_thread.join(timeout=2)
            await pilot.pause()

            assert app.query_one("#oauth-panel").display is False
            runtime.release.set()
    finally:
        runtime.release.set()


@pytest.mark.anyio
async def test_tui_keeps_model_picker_inside_compact_terminal(tmp_path: Path) -> None:
    approval = TuiApproval()
    session = AgentSession(ImmediateModel(), ToolRegistry(()), ToolContext(tmp_path, approval, ()))
    app = TraceAgentTui(session=session, approval=approval, runtime=ImmediateRuntime())

    async with app.run_test(size=(72, 20)) as pilot:
        app.query_one(Input).value = "/model"
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert app.query_one("#model-picker").region.bottom <= 20
        assert app.query_one(Input).region.bottom <= 20


@pytest.mark.anyio
async def test_tui_keeps_approval_controls_inside_compact_terminal(tmp_path: Path) -> None:
    approval = TuiApproval(timeout_seconds=30, mode=PermissionMode.ASK)
    model = RecordingModel(
        (
            ModelTurn(
                "",
                (FunctionCall("call-1", "file_write", {"path": "qa.txt", "content": "qa"}),),
            ),
            ModelTurn("denied", ()),
        ),
        [],
    )
    session = AgentSession(
        model,
        ToolRegistry((FileWriteTool(),)),
        ToolContext(tmp_path, approval, ()),
    )
    app = TraceAgentTui(session=session, approval=approval)

    async with app.run_test(size=(72, 20)) as pilot:
        app.query_one(Input).value = "write file"
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert app.query_one("#approval-panel").region.bottom <= 20
        assert app.query_one("#approve").region.bottom <= 20
        assert app.query_one(Input).region.bottom <= 20

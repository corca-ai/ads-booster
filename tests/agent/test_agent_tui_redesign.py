from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING, final

import pytest
from textual.widgets import Input, OptionList, RichLog, Static

from trace_capture.agent.context import ContextPolicy
from trace_capture.agent.session import AgentSession
from trace_capture.agent.tui import TraceAgentTui
from trace_capture.agent.tui_approval import PermissionMode, TuiApproval
from trace_capture.providers.codex import FunctionCall, ModelTurn
from trace_capture.providers.models import ProviderModel, ProviderReasoningLevel
from trace_capture.tools.filesystem import FileListTool, FileWriteTool
from trace_capture.tools.models import ToolContext
from trace_capture.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from trace_capture.agent.control import AgentControlPort
    from trace_capture.contracts.tools import ToolDescriptor
    from trace_capture.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class RecordingModel:
    turns: tuple[ModelTurn, ...]
    prompts: list[str]

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        _ = tools
        user_content = next(
            item["content"] for item in reversed(history) if item.get("role") == "user"
        )
        turn_index = len(self.prompts)
        self.prompts.append(str(user_content))
        return self.turns[turn_index]


@final
class BlockingOAuthRuntime:
    started: Event
    release: Event

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.model_value = "gpt-test"
        self.reasoning_value: str | None = None
        self.model_catalog = (
            ProviderModel(
                slug="gpt-test",
                display_name="GPT Test",
                default_reasoning_level="low",
                supported_reasoning_levels=(
                    ProviderReasoningLevel(effort="low", description="Fast"),
                    ProviderReasoningLevel(effort="high", description="Deep"),
                ),
            ),
        )

    def oauth_login(
        self,
        on_auth: Callable[[str], None],
        provider: str | None = None,
    ) -> str | None:
        _ = (on_auth, provider)
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
        return self.model_catalog

    def reasoning(self) -> str | None:
        return self.reasoning_value

    def workspace(self) -> str:
        return "."

    def set_model(self, value: str) -> str:
        self.model_value = value
        return self.model_value

    def set_reasoning(self, value: str | None) -> str | None:
        self.reasoning_value = value
        return self.reasoning_value

    def set_workspace(self, value: str) -> str:
        return value


def _app(
    tmp_path: Path,
    model: RecordingModel,
    registry: ToolRegistry | None = None,
    approval: TuiApproval | None = None,
    runtime: AgentControlPort | None = None,
) -> TraceAgentTui:
    tui_approval = approval or TuiApproval()
    context = ToolContext(tmp_path, tui_approval, ())
    session = AgentSession(model, registry or ToolRegistry(()), context)
    return TraceAgentTui(session=session, approval=tui_approval, runtime=runtime)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_tui_does_not_send_removed_oauth_command_to_model(tmp_path: Path) -> None:
    model = RecordingModel((ModelTurn("unexpected", ()),), [])
    app = _app(tmp_path, model)

    async with app.run_test() as pilot:
        app.query_one(Input).value = "/oauth"
        await pilot.press("enter")
        await pilot.pause()

        assert (model.prompts, app.last_response) == ([], None)
        assert "준비됨" in str(app.query_one("#compact-status", Static).renderable)


@pytest.mark.anyio
async def test_tui_first_use_guidance_is_localized_and_actionable(tmp_path: Path) -> None:
    app = _app(tmp_path, RecordingModel((ModelTurn("unused", ()),), []))

    async with app.run_test() as pilot:
        await pilot.pause()

        conversation = str(app.query_one("#conversation", RichLog).lines)
        assert "메시지를 입력해 시작하세요" in conversation
        assert "워크스페이스 흐름: 입장 → 준비 → 새 자료 만들기 → 검수" in conversation
        assert "예: 캠페인 컨텍스트를 정리해줘" in conversation
        assert app.query_one(Input).placeholder == "무엇을 도와드릴까요?"


@pytest.mark.anyio
async def test_tui_cancels_oauth_and_restores_prompt(tmp_path: Path) -> None:
    runtime = BlockingOAuthRuntime()
    model = RecordingModel((ModelTurn("unused", ()),), [])
    app = _app(tmp_path, model, runtime=runtime)

    try:
        async with app.run_test() as pilot:
            app.query_one(Input).value = "/auth login"
            await pilot.press("enter")
            assert runtime.started.wait(timeout=2)

            await pilot.press("escape")
            await pilot.pause()

            assert not app.busy
            assert app.query_one(Input).disabled is False
            assert "준비됨" in str(app.query_one("#compact-status", Static).renderable)
            runtime.release.set()
            await pilot.pause(0.2)
    finally:
        runtime.release.set()


@pytest.mark.anyio
async def test_tui_shows_tool_activity_for_a_completed_tool_call(tmp_path: Path) -> None:
    model = RecordingModel(
        (
            ModelTurn("", (FunctionCall("call-1", "file_list", {"path": "."}),)),
            ModelTurn("done", ()),
        ),
        [],
    )
    app = _app(tmp_path, model, ToolRegistry((FileListTool(),)))

    async with app.run_test() as pilot:
        app.query_one(Input).value = "list files"
        await pilot.press("enter")
        await pilot.pause(0.2)

        activity = app.query_one("#conversation", RichLog)
        assert app.last_response == "done"
        assert len(activity.lines) >= 2
        activity_text = str(activity.lines)
        assert "RUN" in activity_text
        assert "OK" in activity_text
        assert "file_list" in activity_text


@pytest.mark.anyio
async def test_tui_hides_approval_after_timeout(tmp_path: Path) -> None:
    approval = TuiApproval(timeout_seconds=0.01, mode=PermissionMode.ASK)
    model = RecordingModel(
        (
            ModelTurn(
                "",
                (
                    FunctionCall(
                        "call-1",
                        "file_write",
                        {"path": "note.txt", "content": "secret"},
                    ),
                ),
            ),
            ModelTurn("denied", ()),
        ),
        [],
    )
    app = _app(tmp_path, model, ToolRegistry((FileWriteTool(),)), approval)

    async with app.run_test() as pilot:
        app.query_one(Input).value = "write file"
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert app.query_one("#approval-panel").display is False
        assert "준비됨" in str(app.query_one("#compact-status", Static).renderable)
        assert app.last_response == "denied"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("button_id", "expected_response"),
    [("#approve", "approved"), ("#deny", "denied")],
)
async def test_tui_focuses_and_accepts_pending_approval_choice(
    tmp_path: Path,
    button_id: str,
    expected_response: str,
) -> None:
    approval = TuiApproval(timeout_seconds=5, mode=PermissionMode.ASK)
    model = RecordingModel(
        (
            ModelTurn(
                "",
                (
                    FunctionCall(
                        "call-1",
                        "file_write",
                        {"path": "approval.txt", "content": "approved"},
                    ),
                ),
            ),
            ModelTurn(expected_response, ()),
        ),
        [],
    )
    app = _app(tmp_path, model, ToolRegistry((FileWriteTool(),)), approval)

    async with app.run_test(size=(72, 20)) as pilot:
        app.query_one(Input).value = "write file"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.query_one("#approval-panel").display:
                break

        assert app.query_one("#approval-panel").display
        assert app.focused is app.query_one("#approve")
        if button_id == "#deny":
            await pilot.press("tab")
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.last_response is not None:
                break

        assert app.last_response == expected_response


def test_tui_permission_defaults_to_yolo() -> None:
    assert TuiApproval().mode is PermissionMode.YOLO


@pytest.mark.anyio
async def test_tui_permission_command_switches_between_modes(tmp_path: Path) -> None:
    app = _app(tmp_path, RecordingModel((ModelTurn("unused", ()),), []))

    async with app.run_test() as pilot:
        assert app.approval.mode is PermissionMode.YOLO

        for command, expected in (
            ("/permission", "승인 방식: 자동 허용 (yolo)"),
            ("/permission ask", "승인 방식이 매번 확인으로 바뀌었습니다"),
            ("/permission yolo", "승인 방식이 자동 허용으로 바뀌었습니다"),
        ):
            app.query_one(Input).value = command
            await pilot.press("enter")
            await pilot.pause()
            assert expected in str(app.query_one("#conversation", RichLog).lines)

        assert app.approval.mode is PermissionMode.YOLO


@pytest.mark.anyio
async def test_tui_yolo_mode_allows_mutating_tool_without_prompt(tmp_path: Path) -> None:
    app = _app(
        tmp_path,
        RecordingModel(
            (
                ModelTurn(
                    "",
                    (
                        FunctionCall(
                            "call-1",
                            "file_write",
                            {"path": "yolo.txt", "content": "automatic"},
                        ),
                    ),
                ),
                ModelTurn("done", ()),
            ),
            [],
        ),
        ToolRegistry((FileWriteTool(),)),
    )

    async with app.run_test() as pilot:
        app.query_one(Input).value = "write file"
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.last_response is not None:
                break

        assert app.last_response == "done"
        assert app.query_one("#approval-panel").display is False
        assert (tmp_path / "yolo.txt").read_text() == "automatic"


@pytest.mark.anyio
async def test_tui_uses_single_column_without_sidebar(tmp_path: Path) -> None:
    model = RecordingModel((ModelTurn("pong", ()),), [])
    app = _app(tmp_path, model)

    async with app.run_test(size=(72, 20)) as pilot:
        await pilot.pause()

        assert len(app.query("#sidebar")) == 0


@pytest.mark.anyio
async def test_tui_scrolls_long_conversation_from_focused_prompt(tmp_path: Path) -> None:
    app = _app(tmp_path, RecordingModel((ModelTurn("unused", ()),), []))

    async with app.run_test(size=(72, 20)) as pilot:
        conversation = app.query_one("#conversation", RichLog)
        app.write_assistant("\n".join(f"AGENTS.md line {index}" for index in range(80)))
        await pilot.pause()

        assert conversation.max_scroll_y > 0
        bottom = conversation.scroll_y

        await pilot.press("pageup")
        await pilot.pause()

        assert conversation.scroll_y < bottom


@pytest.mark.anyio
async def test_tui_compact_input_still_rejects_unknown_command(tmp_path: Path) -> None:
    model = RecordingModel((ModelTurn("unexpected", ()),), [])
    app = _app(tmp_path, model)

    async with app.run_test(size=(72, 20)) as pilot:
        app.query_one(Input).value = "/stauts 한국어 줄바꿈 폭 검증"
        await pilot.press("enter")
        await pilot.pause()

        assert model.prompts == []
        assert app.last_response is None
        assert "한국어 줄바꿈 폭 검증" in str(app.query_one("#conversation", RichLog).lines)


@pytest.mark.anyio
async def test_tui_rejects_removed_context_command(tmp_path: Path) -> None:
    model = RecordingModel((ModelTurn("pong", ()),), [])
    app = _app(tmp_path, model)

    async with app.run_test() as pilot:
        app.query_one(Input).value = "/context"
        await pilot.press("enter")
        await pilot.pause()

        assert model.prompts == []
        assert app.last_response is None
        assert "알 수 없는 명령어입니다: /context" in str(
            app.query_one("#conversation", RichLog).lines
        )


@pytest.mark.anyio
async def test_tui_renders_context_compaction_activity(tmp_path: Path) -> None:
    app = _app(tmp_path, RecordingModel((ModelTurn("done", ()),), []))
    app.session.context_runtime.policy = ContextPolicy(
        context_window_tokens=120,
        reserved_output_tokens=20,
        soft_ratio=0.5,
        hard_ratio=0.75,
        recent_tail_tokens=20,
    )
    app.session.history.extend(
        [
            {"role": "user", "content": "old marketing brief " + "x" * 160},
            {"role": "assistant", "content": "old decision " + "y" * 160},
        ]
    )

    async with app.run_test() as pilot:
        app.query_one(Input).value = "latest request"
        await pilot.press("enter")
        await pilot.pause(0.3)

        activity_text = str(app.query_one("#conversation", RichLog).lines)
        assert "compaction_started" in activity_text
        assert "flushing_memory" in activity_text


@pytest.mark.anyio
async def test_tui_model_picker_applies_reasoning_effort(tmp_path: Path) -> None:
    runtime = BlockingOAuthRuntime()
    model = RecordingModel((ModelTurn("pong", ()),), [])
    app = _app(tmp_path, model, runtime=runtime)

    async with app.run_test() as pilot:
        app.query_one(Input).value = "/model"
        await pilot.press("enter")
        await pilot.pause(0.2)

        options = app.query_one("#model-options", OptionList)
        assert options.get_option_at_index(0).id == "gpt-test"

        await pilot.press("enter")
        await pilot.pause()

        assert options.get_option_at_index(0).id == "low"
        assert options.get_option_at_index(1).id == "high"

        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert runtime.model_value == "gpt-test"
        assert runtime.reasoning_value == "high"
        assert app.query_one("#model-picker").display is False

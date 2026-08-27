from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.session import AgentSession
from ads_booster.auth.codex import CodexOAuth, DeviceChallenge, OAuthLoginOptions
from ads_booster.auth.models import OAuthCredential
from ads_booster.auth.store import AuthStore
from ads_booster.providers.codex import (
    CodexResponsesClient,
    FunctionCall,
    ModelTurn,
)
from ads_booster.providers.errors import ProviderError
from ads_booster.tools.approval import DenyApproval
from ads_booster.tools.browser import BrowserTool
from ads_booster.tools.filesystem import FileListTool, FileReadTool, FileWriteTool
from ads_booster.tools.models import ToolContext
from ads_booster.tools.registry import ToolRegistry
from ads_booster.tools.shell import ShellTool
from ads_booster.transport.http import HttpResponse

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class RecordingHttp:
    responses: list[HttpResponse]
    json_calls: list[tuple[str, JsonObject]] = field(default_factory=list)
    form_calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = (url, headers)
        message = "unexpected GET request"
        raise AssertionError(message)

    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = headers
        self.json_calls.append((url, payload))
        return self.responses.pop(0)

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = headers
        self.form_calls.append((url, dict(form)))
        return self.responses.pop(0)


@dataclass(frozen=True, slots=True)
class RecordingModel:
    turns: list[ModelTurn]

    def respond(
        self, history: tuple[JsonObject, ...], tools: tuple[ToolDescriptor, ...]
    ) -> ModelTurn:
        _ = (history, tools)
        return self.turns.pop(0)


@dataclass(frozen=True, slots=True)
class AllowApproval:
    actions: list[str] = field(default_factory=list)

    def request(self, action: str, detail: str) -> bool:
        _ = detail
        self.actions.append(action)
        return True


class AdvancingClock:
    _value: float

    def __init__(self) -> None:
        self._value = 0

    def now(self) -> float:
        return self._value

    def sleep(self, seconds: float) -> None:
        self._value += seconds


def credential() -> OAuthCredential:
    return OAuthCredential(
        access_token="access",
        refresh_token="refresh",
        expires_at=10_000,
        account_id="account",
    )


def test_auth_store_round_trip_uses_private_file(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")

    store.save(credential())

    assert store.load() == credential()
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_codex_oauth_refresh_rotates_access_token(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(
        OAuthCredential(
            access_token="access",
            refresh_token="refresh",
            expires_at=1,
            account_id="account",
        )
    )
    http = RecordingHttp(
        responses=[
            HttpResponse(
                200,
                b'{"access_token":"new-access","refresh_token":"new-refresh","expires_in":3600}',
                {},
            )
        ]
    )

    refreshed = CodexOAuth(http=http, store=store, clock=lambda: 100).refresh_if_needed()

    assert refreshed.access_token == "new-access"
    assert refreshed.refresh_token == "new-refresh"
    assert http.form_calls[0][1]["grant_type"] == "refresh_token"


def test_codex_oauth_device_login_persists_credential(tmp_path: Path) -> None:
    clock = AdvancingClock()
    store = AuthStore(tmp_path / "auth.json")
    http = RecordingHttp(
        responses=[
            HttpResponse(200, b'{"device_auth_id":"device","user_code":"ABCD","interval":1}', {}),
            HttpResponse(200, b'{"authorization_code":"code","code_verifier":"verifier"}', {}),
            HttpResponse(
                200,
                b'{"access_token":"access","refresh_token":"refresh","expires_in":3600}',
                {},
            ),
        ]
    )
    challenges: list[DeviceChallenge] = []

    credential_result = CodexOAuth(
        http=http,
        store=store,
        sleep=clock.sleep,
        clock=clock.now,
    ).login(
        options=OAuthLoginOptions(open_browser=False, timeout_seconds=3),
        on_challenge=challenges.append,
    )

    assert credential_result.access_token == "access"
    assert store.load() == credential_result
    assert challenges[0].user_code == "ABCD"


def test_codex_provider_parses_message_response(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(credential())
    http = RecordingHttp(
        responses=[
            HttpResponse(
                200,
                b'{"output":[{"type":"message","content":[{"type":"output_text","text":"pong"}]}]}',
                {},
            )
        ]
    )
    client = CodexResponsesClient(http, CodexOAuth(http, store, clock=lambda: 100))

    result = client.respond(({"role": "user", "content": "ping"},), ())

    assert result.text == "pong"
    assert http.json_calls[0][0].endswith("/responses")
    assert http.json_calls[0][1]["stream"] is True


def test_codex_provider_parses_stream_completion(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(credential())
    body = (
        b"event: response.created\n"
        b'data: {"type":"response.created","response":{"output":[]}}\n\n'
        b"event: response.completed\n"
        b'data: {"type":"response.completed","response":{"output":[{"type":"message",'
        b'"content":[{"type":"output_text","text":"stream pong"}]}]}}\n\n'
    )
    http = RecordingHttp(responses=[HttpResponse(200, body, {"content-type": "text/event-stream"})])
    client = CodexResponsesClient(http, CodexOAuth(http, store, clock=lambda: 100))

    result = client.respond(({"role": "user", "content": "ping"},), ())

    assert result.text == "stream pong"


def test_codex_provider_reconstructs_stream_output_items(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(credential())
    body = (
        b"event: response.output_item.done\n"
        b'data: {"type":"response.output_item.done","item":{"type":"message",'
        b'"content":[{"type":"output_text","text":"item pong"}]}}\n\n'
        b"event: response.completed\n"
        b'data: {"type":"response.completed","response":{"output":[]}}\n\n'
    )
    http = RecordingHttp(responses=[HttpResponse(200, body, {"content-type": "text/event-stream"})])
    client = CodexResponsesClient(http, CodexOAuth(http, store, clock=lambda: 100))

    result = client.respond(({"role": "user", "content": "ping"},), ())

    assert result.text == "item pong"


def test_codex_provider_preserves_safe_http_error_detail(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(credential())
    http = RecordingHttp(
        responses=[HttpResponse(400, b'{"detail":"Unsupported parameter: metadata"}', {})]
    )
    client = CodexResponsesClient(http, CodexOAuth(http, store, clock=lambda: 100))

    with pytest.raises(ProviderError, match="Unsupported parameter: metadata"):
        _ = client.respond(({"role": "user", "content": "ping"},), ())


def test_codex_provider_rejects_empty_turn_with_safe_output_shape(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.save(credential())
    http = RecordingHttp(responses=[HttpResponse(200, b'{"output":[{"type":"reasoning"}]}', {})])
    client = CodexResponsesClient(http, CodexOAuth(http, store, clock=lambda: 100))

    with pytest.raises(ProviderError, match="output_types=reasoning"):
        _ = client.respond(({"role": "user", "content": "ping"},), ())


def test_agent_session_executes_tool_then_returns_final_text(tmp_path: Path) -> None:
    approval = AllowApproval()
    context = ToolContext(tmp_path, approval, ())
    model = RecordingModel(
        turns=[
            ModelTurn("", (FunctionCall("call-1", "file_list", {"path": "."}),)),
            ModelTurn("finished", ()),
        ]
    )
    session = AgentSession(model, ToolRegistry((FileListTool(),)), context)

    result = session.ask("list files")

    assert result == "finished"
    assert any(item.get("type") == "function_call_output" for item in session.history)


def test_filesystem_tools_stay_inside_workspace(tmp_path: Path) -> None:
    approval = AllowApproval()
    context = ToolContext(tmp_path, approval, ())
    writer = FileWriteTool()
    reader = FileReadTool()

    write_result = writer.execute({"path": "note.txt", "content": "hello"}, context)
    read_result = reader.execute({"path": "note.txt"}, context)
    denied_result = reader.execute({"path": "../outside.txt"}, context)

    assert write_result.ok
    assert read_result.output == "hello"
    assert denied_result.error_code == "path_denied"
    assert approval.actions == ["file_write"]


def test_shell_and_browser_mutations_require_approval(tmp_path: Path) -> None:
    context = ToolContext(tmp_path, DenyApproval(), ())

    shell_result = ShellTool().execute({"command": "printf denied"}, context)
    browser_result = BrowserTool().execute({"action": "click", "ref": "@e1"}, context)

    assert shell_result.error_code == "approval_denied"
    assert browser_result.error_code == "approval_denied"


def test_browser_open_uses_configured_browser_command(tmp_path: Path) -> None:
    context = ToolContext(
        tmp_path,
        AllowApproval(),
        (sys.executable, "-c", "import sys; print(' '.join(sys.argv[1:]))"),
    )

    result = BrowserTool().execute({"action": "open", "url": "https://example.com"}, context)

    assert result.ok
    assert "open https://example.com" in result.output

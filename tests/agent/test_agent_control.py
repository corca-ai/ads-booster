from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from ads_booster.agent.control import AgentControl, AgentControlError
from ads_booster.agent.session import AgentSession
from ads_booster.auth.codex import CodexOAuth
from ads_booster.auth.store import AuthStore
from ads_booster.config.settings import AgentSettings
from ads_booster.providers.codex import CodexResponsesClient, ModelTurn
from ads_booster.providers.models import ProviderModel
from ads_booster.tools.approval import DenyApproval
from ads_booster.tools.models import ToolContext
from ads_booster.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ads_booster.contracts.tools import ToolDescriptor
    from ads_booster.transport.http import HttpResponse
    from ads_booster.transport.json_types import JsonObject


@dataclass(frozen=True, slots=True)
class NoopHttp:
    def post_json(
        self,
        url: str,
        payload: JsonObject,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = (url, payload, headers)
        message = "unexpected JSON request"
        raise AssertionError(message)

    def post_form(
        self,
        url: str,
        form: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = (url, form, headers)
        message = "unexpected form request"
        raise AssertionError(message)

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        _ = (url, headers)
        message = "unexpected GET request"
        raise AssertionError(message)


@dataclass(frozen=True, slots=True)
class ImmediateModel:
    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        _ = (history, tools)
        return ModelTurn("pong", ())


def make_control(workspace: Path) -> AgentControl:
    http = NoopHttp()
    oauth = CodexOAuth(http=http, store=AuthStore(workspace / "auth.json"))
    client = CodexResponsesClient(http=http, oauth=oauth)
    context = ToolContext(workspace, DenyApproval(), ())
    session = AgentSession(ImmediateModel(), ToolRegistry(()), context)
    settings = AgentSettings(workspace, "gpt-5.5", ())
    return AgentControl(settings, oauth, client, session)


def test_agent_control_updates_live_model_and_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    control = make_control(workspace)

    assert control.set_model("gpt-test") == "gpt-test"
    assert control.client.model == "gpt-test"
    assert control.set_reasoning("high") == "high"
    assert control.reasoning() == "high"

    new_workspace = tmp_path / "new-workspace"
    new_workspace.mkdir()
    assert control.set_workspace(str(new_workspace)) == str(new_workspace)
    assert control.session.context.workspace == new_workspace
    assert control.settings.workspace == new_workspace


def test_agent_control_exposes_provider_model_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    control = make_control(workspace)
    expected = (
        ProviderModel(
            slug="gpt-5.5",
            display_name="GPT-5.5",
            description="Frontier",
        ),
    )

    def available_models(_client: CodexResponsesClient) -> tuple[ProviderModel, ...]:
        return expected

    monkeypatch.setattr(CodexResponsesClient, "available_models", available_models)

    models = control.models()

    assert models == expected


def test_agent_control_reports_missing_auth_for_provider_model_catalog(tmp_path: Path) -> None:
    control = make_control(tmp_path)

    with pytest.raises(AgentControlError, match="auth_missing"):
        _ = control.models()


def test_agent_control_rejects_missing_workspace(tmp_path: Path) -> None:
    control = make_control(tmp_path)

    with pytest.raises(AgentControlError, match="workspace_invalid"):
        _ = control.set_workspace("missing")

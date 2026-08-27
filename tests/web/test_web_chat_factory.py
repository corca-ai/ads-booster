from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from ads_booster.config.settings import AgentSettings
from ads_booster.providers.codex import CodexResponsesClient
from ads_booster.web.app import create_app
from ads_booster.web.chat_factory import ProductionAgentComponents
from ads_booster.web.schemas import ChatErrorEnvelope
from ads_booster.workspace import SqliteWorkspaceStore

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_chat_returns_typed_auth_error_when_provider_login_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("TRACE_AGENT_HOME", str(tmp_path))
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(
        create_app(tmp_path, session_secret=b"s" * 32),
        base_url="https://testserver",
    )
    login = client.post(
        "/api/auth/login",
        json={
            "workspace_id": workspace.workspace.workspace_id,
            "member_id": member.member.member_id,
            "workspace_code": workspace.access_code,
            "member_code": member.invite_code,
        },
    )
    assert login.status_code == 200

    # When
    response = client.post("/api/chat", json={"prompt": "Plan the next capture"})

    # Then
    error = ChatErrorEnvelope.model_validate_json(response.content)
    assert response.status_code == 503
    assert error.detail.code == "authentication_required"
    assert error.detail.message == "model provider authentication is required"


def test_production_components_preserve_existing_codex_model_and_tool_composition(
    tmp_path: Path,
) -> None:
    # Given
    settings = AgentSettings(
        workspace=tmp_path,
        model="gpt-5.5",
        browser_command=(),
        reasoning_effort="high",
        memory_file=None,
        sessions_dir=tmp_path / "sessions",
    )

    # When
    with ProductionAgentComponents(settings).open() as components:
        tool_names = tuple(tool.name for tool in components.registry.tools)

        # Then
        assert isinstance(components.client, CodexResponsesClient)
        assert components.client.model == settings.model
        assert components.client.reasoning_effort == settings.reasoning_effort
        assert "trace_run" in tool_names
        assert "web_search" in tool_names
        assert components.context.approval.request("write", "shared context") is False

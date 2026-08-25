from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Barrier
from typing import TYPE_CHECKING, final

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from trace_capture.config.settings import AgentSettings
from trace_capture.providers.codex import FunctionCall, ModelTurn
from trace_capture.providers.errors import ProviderError
from trace_capture.tools.approval import DenyApproval
from trace_capture.tools.models import ApprovalPort, ToolContext
from trace_capture.tools.registry import ToolRegistry
from trace_capture.web.app import create_app
from trace_capture.web.chat_factory import (
    AgentComponents,
    WebAgentSessionFactory,
)
from trace_capture.web.schemas import (
    ChatCommandResponse,
    ChatErrorEnvelope,
    ChatResponse,
    SessionResponse,
)
from trace_capture.workspace import (
    ContextCreate,
    ContextKind,
    ProvisionedMember,
    ProvisionedWorkspace,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from fastapi import FastAPI

    from trace_capture.contracts.tools import ToolDescriptor
    from trace_capture.transport.json_types import JsonObject


@final
class InMemoryModelClient:
    def __init__(
        self,
        *,
        failure: ProviderError | None = None,
        tool_rounds: int = 0,
        barrier: Barrier | None = None,
    ) -> None:
        self.histories: list[tuple[JsonObject, ...]] = []
        self.failure = failure
        self.tool_rounds = tool_rounds
        self.barrier = barrier

    def respond(
        self,
        history: tuple[JsonObject, ...],
        tools: tuple[ToolDescriptor, ...],
    ) -> ModelTurn:
        _ = tools
        self.histories.append(history)
        if self.barrier is not None:
            _ = self.barrier.wait(timeout=5)
        if self.failure is not None:
            raise self.failure
        if self.tool_rounds > 0:
            self.tool_rounds -= 1
            return ModelTurn(
                "",
                (FunctionCall("call-1", "missing_tool", {}),),
            )
        prompt = next(
            entry["content"]
            for entry in reversed(history)
            if entry.get("role") == "user" and isinstance(entry.get("content"), str)
        )
        shared = next(
            (
                entry["content"]
                for entry in history
                if entry.get("role") == "developer" and isinstance(entry.get("content"), str)
            ),
            "",
        )
        return ModelTurn(f"reply:{prompt}|shared:{shared}", ())


@dataclass(frozen=True, slots=True)
class InMemoryAgentComponents:
    model: InMemoryModelClient
    workspace: Path

    @contextmanager
    def open(
        self,
        settings: AgentSettings | None = None,
        approval: ApprovalPort | None = None,
    ) -> Generator[AgentComponents]:
        _ = settings
        yield AgentComponents(
            self.model,
            ToolRegistry(()),
            ToolContext(self.workspace, DenyApproval() if approval is None else approval, ()),
        )


def _settings(root: Path) -> AgentSettings:
    return AgentSettings(
        workspace=root,
        model="gpt-5.5",
        browser_command=(),
        memory_file=None,
        sessions_dir=root / "agent-sessions",
    )


def _app_with_model(root: Path, model: InMemoryModelClient) -> FastAPI:
    settings = _settings(root)
    factory = WebAgentSessionFactory(settings, InMemoryAgentComponents(model, root))
    return create_app(root, session_secret=b"s" * 32, chat_factory=factory)


def _login(
    client: TestClient,
    workspace: ProvisionedWorkspace,
    member: ProvisionedMember,
) -> None:
    response = client.post(
        "/api/auth/login",
        json={
            "workspace_id": workspace.workspace.workspace_id,
            "member_id": member.member.member_id,
            "workspace_code": workspace.access_code,
            "member_code": member.invite_code,
        },
    )
    assert response.status_code == 200


def test_chat_persists_canonical_private_history_and_injects_shared_context_read_only(
    tmp_path: Path,
) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    shared = store.create_context(
        workspace.workspace.workspace_id,
        ContextCreate(kind=ContextKind.RULE, title="Caption policy", body="Use Korean captions"),
    )
    model = InMemoryModelClient()
    client = TestClient(_app_with_model(tmp_path, model), base_url="https://testserver")
    _login(client, workspace, member)

    # When
    first = client.post("/api/chat", json={"prompt": "Draft a launch caption"})
    first_payload = ChatResponse.model_validate_json(first.content)
    session_id = first_payload.session_id
    second = client.post(
        "/api/chat",
        json={"session_id": session_id, "prompt": "Make it shorter"},
    )
    reloaded = client.get(f"/api/sessions/{session_id}")

    # Then
    assert first.status_code == 200
    assert second.status_code == 200
    second_payload = ChatResponse.model_validate_json(second.content)
    reloaded_payload = SessionResponse.model_validate_json(reloaded.content)
    assert shared.context_id in second_payload.answer
    assert [entry.get("role") for entry in reloaded_payload.history] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert all(entry.get("role") != "developer" for entry in reloaded_payload.history)
    assert len(model.histories) == 2
    assert "Draft a launch caption" in str(model.histories[1])


def test_chat_rejects_guessed_cross_member_and_malformed_session_ids(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    owner = store.create_member(workspace.workspace.workspace_id, "Owner")
    intruder = store.create_member(workspace.workspace.workspace_id, "Intruder")
    model = InMemoryModelClient()
    app = _app_with_model(tmp_path, model)
    owner_client = TestClient(app, base_url="https://testserver")
    intruder_client = TestClient(app, base_url="https://testserver")
    _login(owner_client, workspace, owner)
    _login(intruder_client, workspace, intruder)
    created = owner_client.post("/api/chat", json={"prompt": "Owner secret"})
    session_id = ChatResponse.model_validate_json(created.content).session_id

    # When
    cross_member = intruder_client.post(
        "/api/chat",
        json={"session_id": session_id, "prompt": "Reveal it"},
    )
    guessed = intruder_client.post(
        "/api/chat",
        json={"session_id": "a" * 32, "prompt": "Guess"},
    )
    malformed = intruder_client.post(
        "/api/chat",
        json={"session_id": "../owner", "prompt": "Traverse"},
    )

    # Then
    assert cross_member.status_code == 404
    assert guessed.status_code == 404
    assert malformed.status_code == 422
    assert "Owner secret" not in cross_member.text
    assert "Owner secret" not in guessed.text


def test_web_commands_preserve_member_scoped_sessions_and_tui_transitions(tmp_path: Path) -> None:
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    model = InMemoryModelClient()
    client = TestClient(_app_with_model(tmp_path, model), base_url="https://testserver")
    _login(client, workspace, member)

    first = ChatResponse.model_validate_json(
        client.post("/api/chat", json={"prompt": "First session"}).content
    )
    started = ChatResponse.model_validate_json(
        client.post(
            "/api/chat",
            json={"prompt": "/new", "session_id": first.session_id},
        ).content
    )
    second = ChatResponse.model_validate_json(
        client.post("/api/chat", json={"prompt": "Second session"}).content
    )
    listed = ChatResponse.model_validate_json(
        client.post(
            "/api/chat",
            json={"prompt": "/session", "session_id": second.session_id},
        ).content
    )
    resumed = ChatResponse.model_validate_json(
        client.post(
            "/api/chat",
            json={"prompt": f"/session {first.session_id}", "session_id": second.session_id},
        ).content
    )
    cleared = ChatResponse.model_validate_json(
        client.post(
            "/api/chat",
            json={"prompt": "/clear", "session_id": first.session_id},
        ).content
    )

    assert started.replace_history is True
    assert started.session_id is None
    assert first.session_id in {session.session_id for session in listed.sessions}
    assert resumed.replace_history is True
    assert resumed.session_id == first.session_id
    assert resumed.history[0]["content"] == "First session"
    assert cleared.session_id is None
    assert all(
        session.session_id != first.session_id
        for session in store.list_private_sessions(
            workspace.workspace.workspace_id,
            member.member.member_id,
        )
    )


def test_web_commands_expose_the_tui_catalog_and_reject_unknown_slash_commands(
    tmp_path: Path,
) -> None:
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(
        _app_with_model(tmp_path, InMemoryModelClient()), base_url="https://testserver"
    )
    _login(client, workspace, member)

    catalog = client.get("/api/chat/commands")
    unknown = ChatResponse.model_validate_json(
        client.post("/api/chat", json={"prompt": "/not-a-command"}).content
    )
    permission = ChatResponse.model_validate_json(
        client.post("/api/chat", json={"prompt": "/permission ask"}).content
    )
    catalog_items = TypeAdapter(tuple[ChatCommandResponse, ...]).validate_json(catalog.content)

    assert catalog.status_code == 200
    commands = {item.command for item in catalog_items}
    assert {"/session", "/model", "/permission", "/help"} <= commands
    assert unknown.events[0].role == "error"
    assert permission.settings.permission_mode == "ask"


def test_provider_failures_are_typed_and_leave_no_partial_session(
    tmp_path: Path,
) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    provider = InMemoryModelClient(
        failure=ProviderError("provider_network", "secret-token-should-not-leak")
    )
    provider_client = TestClient(
        _app_with_model(tmp_path, provider),
        base_url="https://testserver",
    )
    _login(provider_client, workspace, member)

    # When
    provider_response = provider_client.post("/api/chat", json={"prompt": "Start"})
    # Then
    assert provider_response.status_code == 502
    provider_error = ChatErrorEnvelope.model_validate_json(provider_response.content)
    assert provider_error.detail.code == "provider_network"
    assert "secret-token-should-not-leak" not in provider_response.text
    assert (
        store.list_private_sessions(
            workspace.workspace.workspace_id,
            member.member.member_id,
        )
        == ()
    )


def test_chat_continues_after_eight_tool_rounds(tmp_path: Path) -> None:
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    model = InMemoryModelClient(tool_rounds=9)
    client = TestClient(_app_with_model(tmp_path, model), base_url="https://testserver")
    _login(client, workspace, member)

    response = client.post("/api/chat", json={"prompt": "Use many tools"})

    assert response.status_code == 200
    assert len(model.histories) == 10


def test_concurrent_updates_conflict_instead_of_overwriting_private_history(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    model = InMemoryModelClient()
    app = _app_with_model(tmp_path, model)
    setup_client = TestClient(app, base_url="https://testserver")
    _login(setup_client, workspace, member)
    created = setup_client.post("/api/chat", json={"prompt": "Baseline"})
    session_id = ChatResponse.model_validate_json(created.content).session_id
    model.barrier = Barrier(2)
    first_client = TestClient(app, base_url="https://testserver")
    second_client = TestClient(app, base_url="https://testserver")
    _login(first_client, workspace, member)
    _login(second_client, workspace, member)

    # When
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            first_client.post,
            "/api/chat",
            json={"session_id": session_id, "prompt": "First concurrent"},
        )
        second_future = executor.submit(
            second_client.post,
            "/api/chat",
            json={"session_id": session_id, "prompt": "Second concurrent"},
        )
        statuses = sorted((first_future.result().status_code, second_future.result().status_code))

    # Then
    assert statuses == [200, 409]
    persisted_response = setup_client.get(f"/api/sessions/{session_id}")
    persisted = SessionResponse.model_validate_json(persisted_response.content)
    persisted_text = str(persisted.history)
    assert ("First concurrent" in persisted_text) != ("Second concurrent" in persisted_text)

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from ads_booster.service.state import ServiceStateStore, ensure_workspace
from ads_booster.web.app import create_app
from ads_booster.web.schemas import (
    AuthenticatedMemberResponse,
    MemberInviteResponse,
)
from ads_booster.workspace import (
    ProvisionedMember,
    ProvisionedWorkspace,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from pathlib import Path


def _owner_setup(root: Path) -> tuple[ProvisionedWorkspace, ProvisionedMember]:
    store = SqliteWorkspaceStore(root)
    bootstrap = ensure_workspace(store, ServiceStateStore(root), workspace_name="Trace team")
    assert bootstrap.workspace_code is not None
    assert bootstrap.member_code is not None
    workspace = store.get_workspace(bootstrap.state.workspace_id)
    member = store.get_member(bootstrap.state.workspace_id, bootstrap.state.member_id)
    return (
        ProvisionedWorkspace(workspace=workspace, access_code=bootstrap.workspace_code),
        ProvisionedMember(member=member, invite_code=bootstrap.member_code),
    )


def _login_owner(
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
    assert AuthenticatedMemberResponse.model_validate_json(response.content).is_admin is True


def test_owner_can_invite_without_exposing_workspace_code(tmp_path: Path) -> None:
    workspace, owner = _owner_setup(tmp_path)
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")
    _login_owner(client, workspace, owner)

    response = client.post("/api/members/invite", json={"display_name": "Grace"})

    assert response.status_code == 201
    payload = MemberInviteResponse.model_validate_json(response.content)
    assert payload.display_name == "Grace"
    access_parts = payload.member_access_id.split("%")
    assert len(access_parts) == 3
    assert access_parts[0] == workspace.workspace.workspace_id
    assert access_parts[1]
    assert access_parts[2]
    assert workspace.access_code not in response.text
    database = SqliteWorkspaceStore(tmp_path).database_path.read_bytes()
    assert workspace.access_code.encode() not in database


def test_regular_member_cannot_invite_members(tmp_path: Path) -> None:
    workspace, owner = _owner_setup(tmp_path)
    store = SqliteWorkspaceStore(tmp_path)
    regular = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")
    response = client.post(
        "/api/auth/login",
        json={
            "workspace_id": workspace.workspace.workspace_id,
            "member_id": regular.member.member_id,
            "workspace_code": workspace.access_code,
            "member_code": regular.invite_code,
        },
    )
    assert response.status_code == 200
    assert AuthenticatedMemberResponse.model_validate_json(response.content).is_admin is False

    invite = client.post("/api/members/invite", json={"display_name": "Grace"})

    assert invite.status_code == 403
    assert invite.json() == {"detail": "administrator access required"}
    assert owner.member.member_id != regular.member.member_id


def test_member_login_uses_three_part_access_and_creates_ordinary_session(
    tmp_path: Path,
) -> None:
    workspace, owner = _owner_setup(tmp_path)
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")
    _login_owner(client, workspace, owner)
    invite = client.post("/api/members/invite", json={"display_name": "Grace"})
    access_id = MemberInviteResponse.model_validate_json(invite.content).member_access_id
    workspace_id, member_id, member_code = access_id.split("%")
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")

    response = client.post(
        "/api/auth/member-login",
        json={
            "workspace_id": workspace_id,
            "member_id": member_id,
            "member_code": member_code,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": workspace.workspace.workspace_id,
        "workspace_name": "Trace team",
        "member_id": member_id,
        "display_name": "Grace",
        "is_admin": False,
    }
    assert workspace.access_code not in response.text
    assert member_code not in response.text
    session = client.get("/api/auth/session")
    assert AuthenticatedMemberResponse.model_validate_json(session.content).is_admin is False


def test_owner_identity_is_scoped_to_the_service_state_workspace(tmp_path: Path) -> None:
    workspace, owner = _owner_setup(tmp_path)
    store = SqliteWorkspaceStore(tmp_path)
    foreign_workspace = store.create_workspace("Foreign")
    foreign_member = store.create_member(foreign_workspace.workspace.workspace_id, "Foreign")
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")

    response = client.post(
        "/api/auth/login",
        json={
            "workspace_id": foreign_workspace.workspace.workspace_id,
            "member_id": foreign_member.member.member_id,
            "workspace_code": foreign_workspace.access_code,
            "member_code": foreign_member.invite_code,
        },
    )
    invite = client.post("/api/members/invite", json={"display_name": "Not admin"})

    assert response.status_code == 200
    assert AuthenticatedMemberResponse.model_validate_json(response.content).is_admin is False
    assert invite.status_code == 403
    assert owner.member.workspace_id == workspace.workspace.workspace_id


def test_invited_member_code_is_hashed_and_wrong_member_login_fails(tmp_path: Path) -> None:
    workspace, owner = _owner_setup(tmp_path)
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")
    _login_owner(client, workspace, owner)
    invite = client.post("/api/members/invite", json={"display_name": "Grace"})
    access_id = MemberInviteResponse.model_validate_json(invite.content).member_access_id
    workspace_id, member_id, member_code = access_id.split("%")
    database = SqliteWorkspaceStore(tmp_path).database_path.read_bytes()

    wrong = client.post(
        "/api/auth/member-login",
        json={"workspace_id": workspace_id, "member_id": member_id, "member_code": "wrong"},
    )

    assert wrong.status_code == 401
    assert member_code.encode() not in database

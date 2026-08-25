from __future__ import annotations

import base64
import io
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from PIL import Image

from trace_capture.web.app import create_app
from trace_capture.web.schemas import AssetResponse, ContextResponse
from trace_capture.workspace import (
    AssetCreate,
    ContextCreate,
    ContextKind,
    PrivateSessionCreate,
    ProvisionedMember,
    ProvisionedWorkspace,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from pathlib import Path

    from httpx2 import Response


def _login_provisioned(
    client: TestClient,
    workspace: ProvisionedWorkspace,
    member: ProvisionedMember,
    *,
    path: str = "/api/auth/login",
) -> Response:
    return client.post(
        path,
        json={
            "workspace_id": workspace.workspace.workspace_id,
            "member_id": member.member.member_id,
            "workspace_code": workspace.access_code,
            "member_code": member.invite_code,
        },
    )


def test_health_is_available_without_authentication(tmp_path: Path) -> None:
    # Given
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32))

    # When
    response = client.get("/health")

    # Then
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_authenticated_member_can_upload_reference_image(tmp_path: Path) -> None:
    # Given an authenticated member and a valid PNG reference
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")
    _ = _login_provisioned(client, workspace, member)
    buffer = io.BytesIO()
    Image.new("RGB", (4, 6), (80, 120, 160)).save(buffer, format="PNG")

    # When the browser uploads the reference through the authenticated API
    response = client.post(
        "/api/assets/upload",
        json={
            "filename": "exam-desk.png",
            "media_type": "image/png",
            "content_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "context_id": None,
        },
    )

    # Then the server owns the bytes below the asset root and records verified provenance
    assert response.status_code == 201, response.text
    asset = AssetResponse.model_validate_json(response.content)
    path = tmp_path / asset.relative_path
    assert path.read_bytes() == buffer.getvalue()
    assert asset.filename == "exam-desk.png"
    assert asset.media_type == "image/png"


def test_login_uses_hashed_codes_and_sets_a_secure_session_cookie(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(
        create_app(tmp_path, session_secret=b"s" * 32),
        base_url="https://testserver",
    )

    # When
    response = _login_provisioned(client, workspace, member)

    # Then
    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": workspace.workspace.workspace_id,
        "workspace_name": "Trace team",
        "member_id": member.member.member_id,
        "display_name": "Ada",
    }
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" in cookie
    assert workspace.access_code not in response.text
    assert member.invite_code not in response.text
    database_bytes = store.database_path.read_bytes()
    assert workspace.access_code.encode() not in database_bytes
    assert member.invite_code.encode() not in database_bytes


def test_login_rejects_malformed_or_wrong_codes_without_secret_echo(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32))
    malformed = "raw-secret-that-must-not-be-echoed"

    # When
    response = client.post(
        "/api/auth/login",
        json={
            "workspace_id": workspace.workspace.workspace_id,
            "member_id": member.member.member_id,
            "workspace_code": malformed,
            "member_code": member.invite_code,
        },
    )

    # Then
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}
    assert malformed not in response.text
    assert member.invite_code not in response.text


def test_login_compatibility_route_preserves_the_authenticated_member_contract(
    tmp_path: Path,
) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(
        create_app(tmp_path, session_secret=b"s" * 32),
        base_url="https://testserver",
    )

    # When
    response = _login_provisioned(client, workspace, member, path="/login")

    # Then
    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": workspace.workspace.workspace_id,
        "workspace_name": "Trace team",
        "member_id": member.member.member_id,
        "display_name": "Ada",
    }
    assert client.get("/api/sessions").status_code == 200
    assert workspace.access_code not in response.text
    assert member.invite_code not in response.text


def test_context_crud_is_scoped_to_authenticated_workspace(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    first = store.create_workspace("First")
    member = store.create_member(first.workspace.workspace_id, "Ada")
    second = store.create_workspace("Second")
    foreign = store.create_context(
        second.workspace.workspace_id,
        ContextCreate(kind=ContextKind.RULE, title="Foreign", body="Private"),
    )
    client = TestClient(
        create_app(tmp_path, session_secret=b"s" * 32),
        base_url="https://testserver",
    )
    _ = _login_provisioned(client, first, member)

    # When
    created = client.post(
        "/api/contexts",
        json={"kind": "persona", "title": "Launch", "body": "Independent makers"},
    )
    created_context = ContextResponse.model_validate_json(created.content)
    context_id = created_context.context_id
    listed = client.get("/api/contexts")
    loaded = client.get(f"/api/contexts/{context_id}")
    updated = client.put(
        f"/api/contexts/{context_id}",
        json={
            "kind": "promotion",
            "title": "Launch offer",
            "body": "First month free",
            "expected_revision": 1,
        },
    )
    foreign_response = client.get(f"/api/contexts/{foreign.context_id}")
    deleted = client.delete(f"/api/contexts/{context_id}", params={"expected_revision": 2})

    # Then
    assert created.status_code == 201
    assert listed.json() == [created.json()]
    assert loaded.json() == created.json()
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert foreign_response.status_code == 404
    assert deleted.status_code == 204
    assert client.get(f"/api/contexts/{context_id}").status_code == 404


def test_context_compatibility_route_creates_and_lists_only_the_current_workspace(
    tmp_path: Path,
) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Current")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(
        create_app(tmp_path, session_secret=b"s" * 32),
        base_url="https://testserver",
    )
    _ = _login_provisioned(client, workspace, member)

    # When
    created = client.post(
        "/api/context",
        json={"kind": "persona", "title": "Launch", "body": "Independent makers"},
    )
    listed = client.get("/api/context")

    # Then
    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json() == [created.json()]


def test_asset_metadata_crud_is_authenticated_and_workspace_scoped(tmp_path: Path) -> None:
    store = SqliteWorkspaceStore(tmp_path)
    first = store.create_workspace("First")
    ada = store.create_member(first.workspace.workspace_id, "Ada")
    grace = store.create_member(first.workspace.workspace_id, "Grace")
    second = store.create_workspace("Second")
    foreign_asset = store.create_asset(
        second.workspace.workspace_id,
        AssetCreate(
            filename="foreign.png",
            media_type="image/png",
            relative_path="assets/foreign.png",
            sha256="f" * 64,
            size_bytes=10,
        ),
    )
    app = create_app(tmp_path, session_secret=b"s" * 32)
    ada_client = TestClient(app, base_url="https://testserver")
    grace_client = TestClient(app, base_url="https://testserver")
    _ = _login_provisioned(ada_client, first, ada)
    _ = _login_provisioned(grace_client, first, grace)

    created = ada_client.post(
        "/api/assets",
        json={
            "filename": "reference.png",
            "media_type": "image/png",
            "relative_path": "assets/reference.png",
            "sha256": "a" * 64,
            "size_bytes": 123,
        },
    )
    asset_id = AssetResponse.model_validate_json(created.content).asset_id
    listed = grace_client.get("/api/assets")
    updated = ada_client.put(
        f"/api/assets/{asset_id}",
        json={
            "filename": "reference-v2.png",
            "media_type": "image/png",
            "relative_path": "assets/reference-v2.png",
            "sha256": "b" * 64,
            "size_bytes": 456,
        },
    )
    loaded = grace_client.get(f"/api/assets/{asset_id}")
    deleted = grace_client.delete(f"/api/assets/{asset_id}")
    missing = ada_client.get(f"/api/assets/{asset_id}")
    unsafe = ada_client.post(
        "/api/assets",
        json={
            "filename": "escape.png",
            "media_type": "image/png",
            "relative_path": "assets/../escape.png",
            "sha256": "c" * 64,
            "size_bytes": 1,
        },
    )
    foreign = ada_client.get(f"/api/assets/{foreign_asset.asset_id}")

    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()[0]["asset_id"] == asset_id
    assert updated.status_code == 200
    assert updated.json()["filename"] == "reference-v2.png"
    assert loaded.status_code == 200
    assert loaded.json()["sha256"] == "b" * 64
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert unsafe.status_code == 422
    assert foreign.status_code == 404


def test_sessions_are_private_to_authenticated_member(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    owner = store.create_member(workspace.workspace.workspace_id, "Owner")
    intruder = store.create_member(workspace.workspace.workspace_id, "Intruder")
    private = store.create_private_session(
        workspace.workspace.workspace_id,
        owner.member.member_id,
        PrivateSessionCreate(
            title="Owner private",
            history=({"role": "user", "content": "do not leak"},),
        ),
    )
    client = TestClient(
        create_app(tmp_path, session_secret=b"s" * 32),
        base_url="https://testserver",
    )
    _ = _login_provisioned(client, workspace, intruder)

    # When
    listed = client.get("/api/sessions")
    loaded = client.get(f"/api/sessions/{private.session_id}")

    # Then
    assert listed.status_code == 200
    assert listed.json() == []
    assert loaded.status_code == 404
    assert "do not leak" not in loaded.text


def test_run_list_contract_is_authenticated_and_scoped_without_a_storage_seam(
    tmp_path: Path,
) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    first_member = store.create_member(workspace.workspace.workspace_id, "Ada")
    second_member = store.create_member(workspace.workspace.workspace_id, "Grace")
    app = create_app(tmp_path, session_secret=b"s" * 32)
    first_client = TestClient(app, base_url="https://testserver")
    second_client = TestClient(app, base_url="https://testserver")
    anonymous_client = TestClient(app, base_url="https://testserver")
    _ = _login_provisioned(first_client, workspace, first_member)
    _ = _login_provisioned(second_client, workspace, second_member)

    # When
    first_response = first_client.get("/api/runs")
    second_response = second_client.get("/api/runs")
    anonymous_response = anonymous_client.get("/api/runs")

    # Then
    assert first_response.status_code == 200
    assert first_response.json() == []
    assert second_response.status_code == 200
    assert second_response.json() == []
    assert anonymous_response.status_code == 401


def test_expired_tampered_and_revoked_sessions_fail_closed(tmp_path: Path) -> None:
    # Given
    now = [1_000.0]
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    app = create_app(
        tmp_path,
        session_secret=b"s" * 32,
        session_ttl_seconds=60,
        clock=lambda: now[0],
    )
    client = TestClient(app, base_url="https://testserver")
    _ = _login_provisioned(client, workspace, member)
    valid_cookie = client.cookies["trace_session"]

    # When / Then: tampered
    client.cookies.set("trace_session", f"{valid_cookie}x")
    assert client.get("/api/contexts").status_code == 401

    # When / Then: expired
    client.cookies.set("trace_session", valid_cookie)
    now[0] += 61
    assert client.get("/api/contexts").status_code == 401

    # When / Then: revoked by code rotation
    now[0] = 1_000.0
    _ = _login_provisioned(client, workspace, member)
    _ = store.rotate_member_code(
        workspace.workspace.workspace_id,
        member.member.member_id,
        expected_version=member.member.code_version,
    )
    assert client.get("/api/contexts").status_code == 401

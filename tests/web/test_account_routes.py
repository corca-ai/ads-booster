from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from ads_booster.web.app import create_app
from ads_booster.workspace import (
    ProvisionedMember,
    ProvisionedWorkspace,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from pathlib import Path

_IDENTITY: dict[str, Any] = {
    "display_name": "박세나",
    "age": 27,
    "region": "서울",
    "occupation": "병동 간호사",
    "concept": "3교대 근무를 잠금화면 일정으로 버티는 간호사",
    "domain": "office_worker",
    "interests": ["쿠로미", "필라테스"],
    "voice": "반말, 짧은 문장",
    "life_rhythm": "데이 출근일 5시 40분 기상",
    "taste": {
        "background_subject": "character_other",
        "background_mood": "파스텔 톤의 캐릭터 배경",
        "font": "sf_pro_rounded",
    },
}


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


def _client(root: Path, name: str = "Trace") -> TestClient:
    store = SqliteWorkspaceStore(root)
    workspace = store.create_workspace(name)
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(create_app(root, session_secret=b"s" * 32), base_url="https://test")
    _login(client, workspace, member)
    return client


def test_a_created_account_comes_back_in_the_list(tmp_path: Path) -> None:
    client = _client(tmp_path)

    created = client.post("/api/accounts", json={"country": "KR", "identity": _IDENTITY})
    assert created.status_code == 201
    body = created.json()
    assert body["identity"]["taste"]["font"] == "sf_pro_rounded"
    assert body["status"] == "observing"

    listed = client.get("/api/accounts")
    assert listed.status_code == 200
    assert [record["account_id"] for record in listed.json()] == [body["account_id"]]


def test_status_route_records_the_human_verdict(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = client.post("/api/accounts", json={"country": "KR", "identity": _IDENTITY}).json()

    promoted = client.post(
        f"/api/accounts/{created['account_id']}/status",
        json={"status": "active", "expected_revision": created["revision"]},
    )

    assert promoted.status_code == 200
    assert promoted.json()["status"] == "active"
    assert promoted.json()["revision"] == created["revision"] + 1


def test_a_stale_revision_conflicts_instead_of_overwriting(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = client.post("/api/accounts", json={"country": "KR", "identity": _IDENTITY}).json()
    _ = client.post(
        f"/api/accounts/{created['account_id']}/status",
        json={"status": "active", "expected_revision": created["revision"]},
    )

    conflicted = client.put(
        f"/api/accounts/{created['account_id']}",
        json={
            "identity": _IDENTITY,
            "note": "다시 씀",
            "expected_revision": created["revision"],
        },
    )

    assert conflicted.status_code == 409


def test_an_unknown_account_is_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.get("/api/accounts/missing").status_code == 404


def test_accounts_require_an_authenticated_member(tmp_path: Path) -> None:
    client = TestClient(
        create_app(tmp_path, session_secret=b"s" * 32),
        base_url="https://test",
    )

    assert client.get("/api/accounts").status_code == 401

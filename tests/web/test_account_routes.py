from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from ads_booster.candidate_generation import CandidateBatch
from ads_booster.web.app import create_app
from ads_booster.workspace import (
    CandidateCreate,
    CandidateImageInputs,
    CandidateSource,
    MarketingAccountId,
    ProvisionedMember,
    ProvisionedWorkspace,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.workspace import MarketingAccountRecord, WorkspaceId


@dataclass(frozen=True, slots=True)
class RecordingGenerator:
    """Stands in for generation so the test can see which account reached it."""

    seen_accounts: list[MarketingAccountRecord | None] = field(default_factory=list)

    def generate(
        self,
        workspace_id: WorkspaceId,
        *,
        run_context: str | None = None,
        account: MarketingAccountRecord | None = None,
    ) -> CandidateBatch:
        del workspace_id, run_context
        self.seen_accounts.append(account)
        return CandidateBatch(records=())


_SCHEDULE: dict[str, Any] = {"language": "ko", "timezone": "Asia/Seoul"}
_IDENTITY: dict[str, Any] = {
    "display_name": "박세나",
    "age": 27,
    "region": "서울",
    "occupation": "병동 간호사",
    "concept": "3교대 근무를 잠금화면 일정으로 버티는 간호사",
    "domain": "office_worker",
    "interests": ["쿠로미", "필라테스"],
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

    created = client.post(
        "/api/accounts", json={"country": "KR", "identity": _IDENTITY, "schedule": _SCHEDULE}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["identity"]["taste"]["font"] == "sf_pro_rounded"
    assert body["status"] == "observing"
    assert body["display_name"] == "박세나"
    assert body["language"] == "ko"

    listed = client.get("/api/accounts")
    assert listed.status_code == 200
    assert [record["account_id"] for record in listed.json()] == [body["account_id"]]


def test_status_route_records_the_human_verdict(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = client.post(
        "/api/accounts", json={"country": "KR", "identity": _IDENTITY, "schedule": _SCHEDULE}
    ).json()

    promoted = client.post(
        f"/api/accounts/{created['account_id']}/status",
        json={"status": "active", "expected_revision": created["revision"]},
    )

    assert promoted.status_code == 200
    assert promoted.json()["status"] == "active"
    assert promoted.json()["revision"] == created["revision"] + 1


def test_a_stale_revision_conflicts_instead_of_overwriting(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = client.post(
        "/api/accounts", json={"country": "KR", "identity": _IDENTITY, "schedule": _SCHEDULE}
    ).json()
    _ = client.post(
        f"/api/accounts/{created['account_id']}/status",
        json={"status": "active", "expected_revision": created["revision"]},
    )

    conflicted = client.put(
        f"/api/accounts/{created['account_id']}",
        json={
            "identity": _IDENTITY,
            "schedule": _SCHEDULE,
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


def test_generation_is_written_as_the_chosen_account(tmp_path: Path) -> None:
    """The account a batch is generated for reaches the generator, not just the URL."""
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    generator = RecordingGenerator()
    client = TestClient(
        create_app(tmp_path, session_secret=b"s" * 32, candidate_generator=generator),
        base_url="https://test",
    )
    _login(client, workspace, member)
    created = client.post(
        "/api/accounts",
        json={"country": "KR", "identity": _IDENTITY, "schedule": _SCHEDULE},
    ).json()

    response = client.post(
        f"/api/candidates/generate?account_id={created['account_id']}",
    )

    assert response.status_code == 201
    assert generator.seen_accounts[-1] is not None
    assert generator.seen_accounts[-1].account_id == created["account_id"]
    assert generator.seen_accounts[-1].identity.occupation == "병동 간호사"


def test_generation_without_an_account_stays_workspace_wide(tmp_path: Path) -> None:
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    generator = RecordingGenerator()
    client = TestClient(
        create_app(tmp_path, session_secret=b"s" * 32, candidate_generator=generator),
        base_url="https://test",
    )
    _login(client, workspace, member)

    assert client.post("/api/candidates/generate").status_code == 201
    assert generator.seen_accounts == [None]


def test_generating_for_an_unknown_account_is_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.post("/api/candidates/generate?account_id=missing").status_code == 404


def test_each_account_sees_only_its_own_candidates(tmp_path: Path) -> None:
    """Two accounts in one workspace must not share a draft list."""
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")
    _login(client, workspace, member)
    first = client.post(
        "/api/accounts",
        json={"country": "KR", "identity": _IDENTITY, "schedule": _SCHEDULE},
    ).json()
    second_identity = {**_IDENTITY, "display_name": "이서진", "occupation": "1인 개발자"}
    second = client.post(
        "/api/accounts",
        json={"country": "KR", "identity": second_identity, "schedule": _SCHEDULE},
    ).json()

    store.create_candidate(
        CandidateCreate(
            workspace_id=workspace.workspace.workspace_id,
            account_id=MarketingAccountId(first["account_id"]),
            source=CandidateSource.AUTO,
            country="KR",
            topic="첫 계정의 주제",
            caption="첫 계정의 캡션",
            hypothesis="가설",
            shooting_order="",
            image_inputs=CandidateImageInputs(
                trace_items=("19:00 직관",),
                device_time="18:10",
                background_intent="야간 경기 조명이 켜진 외야 관중석",
                language="ko",
            ),
        )
    )

    mine = client.get(f"/api/candidates?account_id={first['account_id']}").json()
    theirs = client.get(f"/api/candidates?account_id={second['account_id']}").json()

    assert [record["topic"] for record in mine] == ["첫 계정의 주제"]
    assert theirs == []
    assert len(client.get("/api/candidates").json()) == 1

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from ads_booster.candidate_generation import AccountProposal, CandidateBatch
from ads_booster.candidate_generation.errors import CandidateFormatError
from ads_booster.web.app import create_app
from ads_booster.web.schemas import AccountProposalResponse
from ads_booster.workspace import (
    CandidateBackgroundSubject,
    CandidateCreate,
    CandidateImageInputs,
    CandidatePersonaDomain,
    CandidateSource,
    LockScreenFont,
    MarketingAccountId,
    MarketingAccountIdentity,
    MarketingAccountTaste,
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


@dataclass(frozen=True, slots=True)
class RecordingProposals:
    """Stands in for the model so the test can read the prompt it was given."""

    proposals: tuple[AccountProposal, ...] = ()
    failure: Exception | None = None
    seen: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def propose(
        self,
        country: str,
        existing: tuple[MarketingAccountRecord, ...] = (),
    ) -> tuple[AccountProposal, ...]:
        self.seen.append((country, tuple(record.identity.display_name for record in existing)))
        if self.failure is not None:
            raise self.failure
        return self.proposals


_PROPOSALS: TypeAdapter[list[AccountProposalResponse]] = TypeAdapter(list[AccountProposalResponse])


def _proposal(name: str = "이서진") -> AccountProposal:
    return AccountProposal(
        identity=MarketingAccountIdentity(
            display_name=name,
            age=27,
            region="서울 마포구",
            occupation="병동 간호사",
            concept="3교대를 잠금화면 일정으로 버티는 간호사",
            domain=CandidatePersonaDomain.OFFICE_WORKER,
            interests=("쿠로미", "필라테스", "동네 베이커리"),
            life_rhythm="데이 근무일은 5시 40분 기상",
            taste=MarketingAccountTaste(
                background_subject=CandidateBackgroundSubject.CHARACTER_OTHER,
                background_mood="파스텔 톤 캐릭터 화면",
                font=LockScreenFont.SF_PRO_ROUNDED,
            ),
        ),
        reason="kr-014·kr-003처럼 질문형 훅이 도달을 만든 사례가 있다",
    )


def _proposal_client(root: Path, proposals: RecordingProposals) -> TestClient:
    store = SqliteWorkspaceStore(root)
    workspace = store.create_workspace("Trace")
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    client = TestClient(
        create_app(root, session_secret=b"s" * 32, account_proposals=proposals),
        base_url="https://test",
    )
    _login(client, workspace, member)
    return client


def test_proposals_answer_in_the_shape_the_create_form_submits(tmp_path: Path) -> None:
    """A proposal is only useful if it can fill the form without translation."""
    # Given a generator that suggests one account
    proposals = RecordingProposals(proposals=(_proposal(),))
    client = _proposal_client(tmp_path, proposals)

    # When proposals are asked for
    response = client.post("/api/accounts/proposals", json={"country": "KR"})

    # Then the identity comes back whole, with the evidence the reviewer reads
    assert response.status_code == 200, response.text
    suggested = _PROPOSALS.validate_json(response.content)
    assert len(suggested) == 1
    assert suggested[0].identity.display_name == "이서진"
    assert suggested[0].identity.taste.font is LockScreenFont.SF_PRO_ROUNDED
    assert "kr-014" in suggested[0].reason
    # And the same payload is accepted by the create route unchanged.
    created = client.post(
        "/api/accounts",
        json={
            "country": "KR",
            "identity": suggested[0].identity.model_dump(mode="json"),
            "schedule": _SCHEDULE,
        },
    )
    assert created.status_code == 201, created.text


def test_proposals_are_shown_the_accounts_that_already_exist(tmp_path: Path) -> None:
    """Suggesting a person who is already posting wastes the reviewer's only decision."""
    # Given a workspace that already runs one account
    proposals = RecordingProposals(proposals=(_proposal("김도현"),))
    client = _proposal_client(tmp_path, proposals)
    _ = client.post(
        "/api/accounts", json={"country": "KR", "identity": _IDENTITY, "schedule": _SCHEDULE}
    )

    # When proposals are asked for
    _ = client.post("/api/accounts/proposals", json={"country": "KR"})

    # Then the generator was told what is already running, and for which country
    country, existing = proposals.seen[0]
    assert country == "KR"
    assert existing == (_IDENTITY["display_name"],)


def test_nothing_is_stored_by_asking_for_proposals(tmp_path: Path) -> None:
    """A suggestion nobody picked should leave no trace."""
    # Given a generator with two suggestions
    client = _proposal_client(
        tmp_path, RecordingProposals(proposals=(_proposal(), _proposal("박세나")))
    )

    # When proposals are asked for but none is chosen
    assert client.post("/api/accounts/proposals", json={"country": "KR"}).status_code == 200

    # Then the account list is untouched
    assert client.get("/api/accounts").json() == []


def test_a_failed_proposal_is_reported_rather_than_half_answered(tmp_path: Path) -> None:
    """The button has to be able to say why it produced nothing."""
    # Given a generator whose model answer does not parse
    client = _proposal_client(
        tmp_path, RecordingProposals(failure=CandidateFormatError("proposals were not JSON"))
    )

    # When proposals are asked for
    response = client.post("/api/accounts/proposals", json={"country": "KR"})

    # Then the typed failure reaches the browser as its own status and Korean message
    assert response.status_code == 502
    assert "형식을 통과하지 못했습니다" in response.json()["detail"]


def test_proposals_require_an_authenticated_member(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path, session_secret=b"s" * 32), base_url="https://test")

    assert client.post("/api/accounts/proposals", json={"country": "KR"}).status_code == 401

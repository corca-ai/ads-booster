from __future__ import annotations

# noqa: SIZE_OK -- the two-stage candidate journey shares authenticated route fixtures
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from ads_booster.agent.runs import (
    AgentGoal,
    AgentRun,
    AgentRunAlreadyExistsError,
    AgentRunId,
    AgentRunState,
    AgentRunStore,
    AgentRunUpdate,
    ConnectorId,
    ToolPolicy,
)
from ads_booster.candidate_generation import (
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateFormatError,
    CandidateGenerationError,
    CandidateImageStageError,
    CandidateRunConflictError,
)
from ads_booster.web.app import create_app
from ads_booster.workspace import (
    CandidateCreate,
    CandidateId,
    CandidateImageAttachment,
    CandidateImageInputs,
    CandidateSource,
    CandidateStatus,
    MarketingAccountRecord,
    ProvisionedMember,
    ProvisionedWorkspace,
    SqliteWorkspaceStore,
)

from ads_booster.web.schemas import CandidateResponse  # isort: skip

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject
    from ads_booster.workspace import CandidateRecord, WorkspaceId

_CANDIDATES = TypeAdapter(tuple[CandidateResponse, ...])


@dataclass(frozen=True, slots=True)
class FakeGenerator:
    store: SqliteWorkspaceStore | None = None
    failure: CandidateGenerationError | None = None

    seen_accounts: list[MarketingAccountRecord | None] = field(default_factory=list)

    def generate(
        self,
        workspace_id: WorkspaceId,
        *,
        run_context: str | None = None,
        account: MarketingAccountRecord | None = None,
    ) -> tuple[CandidateRecord, ...]:
        del run_context
        self.seen_accounts.append(account)
        if self.failure is not None:
            raise self.failure
        assert self.store is not None
        return tuple(
            self.store.create_candidate(
                CandidateCreate(
                    workspace_id=workspace_id,
                    source=CandidateSource.AUTO,
                    country="KR",
                    topic=f"자동 주제 {index}",
                    caption=f"자동 캡션 {index}",
                    hypothesis="자동 가설",
                    image_inputs=CandidateImageInputs(
                        trace_items=("09:00 통계학 2교시", "13:00 스터디"),
                        device_time="07:20",
                        background_intent="늦은 밤 책상 위 스탠드 불빛이 보이는 실제 공부방",
                        language="ko",
                    ),
                    refs_used=("kr-001",),
                    principles_applied=(1,),
                    shooting_order="입력_일정: 9시 스터디",
                )
            )
            for index in range(3)
        )


def _login(client: TestClient, workspace: ProvisionedWorkspace, member: ProvisionedMember) -> None:
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


@dataclass(frozen=True, slots=True)
class FakeImageRunner:
    store: SqliteWorkspaceStore | None = None
    failure: Exception | None = None
    image_bytes: bytes = b"\x89PNG\r\n\x1a\n composed"

    def generate(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> CandidateRecord:
        if self.failure is not None:
            raise self.failure
        assert self.store is not None
        record = self.store.get_candidate(workspace_id, candidate_id)
        run_id = AgentRunId(f"candidate-{candidate_id}-r{record.revision}")
        agent_runs = AgentRunStore(self.store.database_path.parent / "core-agent")
        try:
            queued = agent_runs.create(
                AgentRun(
                    run_id=run_id,
                    connector_id=ConnectorId("trace-marketing"),
                    connector_version="1.0.0",
                    goal=AgentGoal(
                        objective="Create one candidate image",
                        success_criteria=("human review",),
                    ),
                    tool_policy=ToolPolicy(allow=("trace_generate_marketing_image",)),
                ),
                now=1.0,
            )
            running = agent_runs.update(
                run_id,
                AgentRunUpdate(
                    expected_revision=queued.revision,
                    state=AgentRunState.RUNNING,
                    at=2.0,
                ),
            )
            _ = agent_runs.update(
                run_id,
                AgentRunUpdate(
                    expected_revision=running.revision,
                    state=AgentRunState.AWAITING_APPROVAL,
                    at=3.0,
                ),
            )
        except AgentRunAlreadyExistsError:
            _ = agent_runs.get(run_id)
        relative = f"candidates/{candidate_id}/r{record.revision}/outputs/final.png"
        path = self.store.database_path.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(self.image_bytes)
        return self.store.attach_candidate_image(
            workspace_id,
            candidate_id,
            CandidateImageAttachment(
                path=relative,
                sha256=sha256(self.image_bytes).hexdigest(),
                agent_run_id=run_id,
                expected_revision=record.revision,
            ),
        )


def _client(
    root: Path,
    store: SqliteWorkspaceStore,
    name: str,
    generator: FakeGenerator | None = None,
    image_runner: FakeImageRunner | None = None,
) -> TestClient:
    workspace = store.create_workspace(name)
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    app = create_app(
        root,
        session_secret=b"s" * 32,
        candidate_generator=FakeGenerator(store) if generator is None else generator,
        candidate_image_runner=FakeImageRunner(store) if image_runner is None else image_runner,
    )
    client = TestClient(app, base_url="https://test")
    _login(client, workspace, member)
    return client


def _caption_approved(client: TestClient) -> CandidateResponse:
    created = CandidateResponse.model_validate_json(
        client.post("/api/candidates", json=_payload()).content
    )
    reviewed = client.post(
        f"/api/candidates/{created.candidate_id}/review",
        json={"accepted": True, "note": None, "expected_revision": created.revision},
    )
    assert reviewed.status_code == 200, reviewed.text
    return CandidateResponse.model_validate_json(reviewed.content)


def _payload(caption: str = "시험 기간엔 잠금화면부터 바꾼다") -> JsonObject:
    return {
        "topic": "시험기간 일정 관리 — 잠금화면 데모로 공감 훅",
        "country": "JP",
        "posting_slot": "evening",
        "caption": caption,
        "hypothesis": "1인칭 감탄이 저장률을 올린다",
        "image_inputs": {
            "trace_items": ["09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"],
            "device_time": "07:20",
            "background_subject": "scenery",
            "background_mood": "늦은 밤 책상 위 스탠드 불빛",
            "language": "ko",
        },
        "refs_used": ["ref-a", "ref-b"],
        "principles_applied": [1, 4],
        "shooting_order": "- 책상 위 아이폰\n- 형광펜 두 자루",
    }


def test_manual_candidate_is_created_and_listed_for_the_workspace(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")

    # When
    created = client.post("/api/candidates", json=_payload())
    listed = client.get("/api/candidates")

    # Then
    assert created.status_code == 201, created.text
    record = CandidateResponse.model_validate_json(created.content)
    assert record.source is CandidateSource.MANUAL
    assert record.topic == "시험기간 일정 관리 — 잠금화면 데모로 공감 훅"
    assert record.posting_slot == "evening"
    assert record.status is CandidateStatus.AWAITING_REVIEW
    assert record.refs_used == ("ref-a", "ref-b")
    assert record.principles_applied == (1, 4)
    assert record.ai_verdict is None
    assert listed.status_code == 200
    assert _CANDIDATES.validate_json(listed.content) == (record,)


def test_manual_candidate_rejects_a_malformed_country(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    payload = _payload() | {"country": "japan"}

    # When
    response = client.post("/api/candidates", json=payload)

    # Then
    assert response.status_code == 422
    assert client.get("/api/candidates").json() == []


def test_manual_candidate_requires_a_topic(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    missing = {key: value for key, value in _payload().items() if key != "topic"}

    # When
    blank = client.post("/api/candidates", json=_payload() | {"topic": ""})
    absent = client.post("/api/candidates", json=missing)

    # Then
    assert blank.status_code == 422
    assert absent.status_code == 422
    assert client.get("/api/candidates").json() == []


def test_approval_and_rejection_persist_the_reviewed_state(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    accepted_id = CandidateResponse.model_validate_json(
        client.post("/api/candidates", json=_payload("승인될 캡션")).content
    ).candidate_id
    rejected_id = CandidateResponse.model_validate_json(
        client.post("/api/candidates", json=_payload("반려될 캡션")).content
    ).candidate_id

    # When
    approved = client.post(
        f"/api/candidates/{accepted_id}/review",
        json={"accepted": True, "note": None, "expected_revision": 1},
    )
    rejected = client.post(
        f"/api/candidates/{rejected_id}/review",
        json={"accepted": False, "note": "1인칭 감탄이 빠졌습니다", "expected_revision": 1},
    )

    # Then
    assert approved.status_code == 200, approved.text
    assert rejected.status_code == 200, rejected.text
    approved_record = CandidateResponse.model_validate_json(approved.content)
    rejected_record = CandidateResponse.model_validate_json(rejected.content)
    assert approved_record.status is CandidateStatus.CAPTION_APPROVED
    assert approved_record.review_note is None
    assert approved_record.revision == 2
    assert rejected_record.status is CandidateStatus.REJECTED
    assert rejected_record.review_note == "1인칭 감탄이 빠졌습니다"


def test_unknown_candidate_review_is_not_found(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")

    # When
    response = client.post(
        "/api/candidates/missing-candidate/review",
        json={"accepted": True, "note": None, "expected_revision": 1},
    )

    # Then
    assert response.status_code == 404
    assert response.json() == {"detail": "candidate not found"}


def test_stale_revision_review_conflicts(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    candidate_id = CandidateResponse.model_validate_json(
        client.post("/api/candidates", json=_payload()).content
    ).candidate_id

    # When
    response = client.post(
        f"/api/candidates/{candidate_id}/review",
        json={"accepted": True, "note": None, "expected_revision": 7},
    )

    # Then
    assert response.status_code == 409
    assert response.json() == {"detail": "candidate revision conflict"}


def test_second_review_conflicts_instead_of_overwriting(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    candidate_id = CandidateResponse.model_validate_json(
        client.post("/api/candidates", json=_payload()).content
    ).candidate_id
    first = client.post(
        f"/api/candidates/{candidate_id}/review",
        json={"accepted": True, "note": None, "expected_revision": 1},
    )
    assert first.status_code == 200

    # When
    second = client.post(
        f"/api/candidates/{candidate_id}/review",
        json={"accepted": False, "note": "다시 반려", "expected_revision": 2},
    )

    # Then
    assert second.status_code == 409
    assert second.json() == {"detail": "candidate already reviewed"}
    listed = _CANDIDATES.validate_json(client.get("/api/candidates").content)
    assert listed[0].status is CandidateStatus.CAPTION_APPROVED


def test_candidates_are_not_visible_or_reviewable_across_workspaces(tmp_path: Path) -> None:
    # Given two workspaces in the same service, each with an authenticated member
    store = SqliteWorkspaceStore(tmp_path)
    owner = _client(tmp_path, store, "First")
    other = _client(tmp_path, store, "Second")
    candidate_id = CandidateResponse.model_validate_json(
        owner.post("/api/candidates", json=_payload()).content
    ).candidate_id

    # When the second workspace lists and reviews the first workspace's candidate
    listed = other.get("/api/candidates")
    review = other.post(
        f"/api/candidates/{candidate_id}/review",
        json={"accepted": True, "note": None, "expected_revision": 1},
    )

    # Then the candidate stays private to its own workspace and awaiting review
    assert listed.json() == []
    assert review.status_code == 404
    owned = _CANDIDATES.validate_json(owner.get("/api/candidates").content)
    assert owned[0].status is CandidateStatus.AWAITING_REVIEW


def test_generate_stores_three_automatic_candidates(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")

    # When
    response = client.post("/api/candidates/generate")

    # Then
    assert response.status_code == 201, response.text
    created = _CANDIDATES.validate_json(response.content)
    assert len(created) == 3
    assert all(record.source is CandidateSource.AUTO for record in created)
    assert all(record.status is CandidateStatus.AWAITING_REVIEW for record in created)
    listed = _CANDIDATES.validate_json(client.get("/api/candidates").content)
    assert len(listed) == 3


def test_generate_requires_an_authenticated_member(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    _ = store.create_workspace("Trace team")
    client = TestClient(
        create_app(tmp_path, session_secret=b"s" * 32, candidate_generator=FakeGenerator(store)),
        base_url="https://test",
    )

    # When
    response = client.post("/api/candidates/generate")

    # Then
    assert response.status_code == 401


def test_generate_reports_a_missing_context_folder_as_a_conflict(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    failure = CandidateContextMissingError(tmp_path / "context")
    client = _client(tmp_path, store, "Trace team", FakeGenerator(failure=failure))

    # When
    response = client.post("/api/candidates/generate")

    # Then
    assert response.status_code == 409
    assert response.json() == {"detail": failure.message}
    assert "context 폴더를 찾을 수 없습니다" in response.json()["detail"]


def test_generate_reports_a_missing_credential_as_a_conflict(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(
        tmp_path,
        store,
        "Trace team",
        FakeGenerator(failure=CandidateAuthRequiredError()),
    )

    # When
    response = client.post("/api/candidates/generate")

    # Then
    assert response.status_code == 409
    assert "trace-agent auth login" in response.json()["detail"]


def test_generate_reports_a_format_failure_as_a_bad_gateway(tmp_path: Path) -> None:
    # Given
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(
        tmp_path,
        store,
        "Trace team",
        FakeGenerator(failure=CandidateFormatError("두 번째 응답도 배열이 아닙니다")),
    )

    # When
    response = client.post("/api/candidates/generate")

    # Then
    assert response.status_code == 502
    assert response.json() == {
        "detail": "AI 응답이 형식을 통과하지 못했습니다 — 다시 시도해 주세요."
    }
    assert client.get("/api/candidates").json() == []


def test_generate_image_composes_and_moves_to_image_review(tmp_path: Path) -> None:
    # Given a caption-approved candidate
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    approved = _caption_approved(client)

    # When the image stage runs
    response = client.post(f"/api/candidates/{approved.candidate_id}/generate-image")

    # Then the candidate carries a verified image and waits for image review
    assert response.status_code == 201, response.text
    composed = CandidateResponse.model_validate_json(response.content)
    assert composed.status is CandidateStatus.IMAGE_AWAITING_REVIEW
    assert composed.image_path is not None
    assert composed.image_sha256 is not None


def test_generate_image_rejects_a_candidate_that_is_not_caption_approved(tmp_path: Path) -> None:
    # Given a candidate still awaiting caption review
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    created = CandidateResponse.model_validate_json(
        client.post("/api/candidates", json=_payload()).content
    )

    # When
    response = client.post(f"/api/candidates/{created.candidate_id}/generate-image")

    # Then
    assert response.status_code == 409
    assert response.json() == {"detail": "candidate is not caption approved"}


def test_generate_image_reports_a_stage_failure_verbatim(tmp_path: Path) -> None:
    # Given an image stage that cannot find its component fixture
    store = SqliteWorkspaceStore(tmp_path)
    failure = CandidateImageStageError("잠금화면 부품 이미지를 찾을 수 없습니다 (경로: /x)")
    client = _client(tmp_path, store, "Trace team", image_runner=FakeImageRunner(failure=failure))
    approved = _caption_approved(client)

    # When
    response = client.post(f"/api/candidates/{approved.candidate_id}/generate-image")

    # Then
    assert response.status_code == 409
    assert response.json() == {"detail": failure.message}


def test_generate_image_reports_an_already_running_agent_as_a_conflict(tmp_path: Path) -> None:
    # Given an image run that is already serving another request. The kernel adapter is
    # what turns the runtime's refusal into this error; see the image-stage tests.
    store = SqliteWorkspaceStore(tmp_path)
    failure = CandidateRunConflictError()
    client = _client(tmp_path, store, "Trace team", image_runner=FakeImageRunner(failure=failure))
    approved = _caption_approved(client)

    # When the same image generation is requested again
    response = client.post(f"/api/candidates/{approved.candidate_id}/generate-image")

    # Then the API returns JSON instead of leaking a plain-text server error
    assert response.status_code == 409
    assert response.json() == {"detail": "candidate Agent run conflict"}


def test_image_approval_submits_and_rejection_returns_for_a_new_image(tmp_path: Path) -> None:
    # Given two composed candidates
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    first = _caption_approved(client)
    second = _caption_approved(client)
    approved = CandidateResponse.model_validate_json(
        client.post(f"/api/candidates/{first.candidate_id}/generate-image").content
    )
    rejected = CandidateResponse.model_validate_json(
        client.post(f"/api/candidates/{second.candidate_id}/generate-image").content
    )

    # When
    submit = client.post(
        f"/api/candidates/{approved.candidate_id}/review-image",
        json={"accepted": True, "note": None, "expected_revision": approved.revision},
    )
    send_back = client.post(
        f"/api/candidates/{rejected.candidate_id}/review-image",
        json={
            "accepted": False,
            "note": "배경이 너무 어둡습니다",
            "expected_revision": rejected.revision,
        },
    )

    # Then
    assert submit.status_code == 200, submit.text
    assert send_back.status_code == 200, send_back.text
    submitted = CandidateResponse.model_validate_json(submit.content)
    returned = CandidateResponse.model_validate_json(send_back.content)
    assert submitted.status is CandidateStatus.SUBMITTED
    assert submitted.image_path == approved.image_path
    assert returned.status is CandidateStatus.CAPTION_APPROVED
    assert returned.review_note == "배경이 너무 어둡습니다"
    assert returned.image_path is None
    agent_runs = AgentRunStore(tmp_path / "core-agent")
    assert submitted.agent_run_id is not None
    assert returned.agent_run_id is not None
    assert agent_runs.get(AgentRunId(submitted.agent_run_id)).state is AgentRunState.COMPLETED
    assert agent_runs.get(AgentRunId(returned.agent_run_id)).state is AgentRunState.QUEUED


def test_image_review_requires_a_candidate_at_the_image_gate(tmp_path: Path) -> None:
    # Given a caption-approved candidate with no composed image
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    approved = _caption_approved(client)

    # When
    response = client.post(
        f"/api/candidates/{approved.candidate_id}/review-image",
        json={"accepted": True, "note": None, "expected_revision": approved.revision},
    )

    # Then
    assert response.status_code == 409
    assert response.json() == {"detail": "candidate has no image awaiting review"}


def test_stale_image_review_conflicts(tmp_path: Path) -> None:
    # Given a composed candidate
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    approved = _caption_approved(client)
    composed = CandidateResponse.model_validate_json(
        client.post(f"/api/candidates/{approved.candidate_id}/generate-image").content
    )

    # When a stale revision reviews it
    response = client.post(
        f"/api/candidates/{composed.candidate_id}/review-image",
        json={"accepted": True, "note": None, "expected_revision": composed.revision + 5},
    )

    # Then
    assert response.status_code == 409
    assert response.json() == {"detail": "candidate revision conflict"}


def test_candidate_image_is_served_to_its_own_workspace_only(tmp_path: Path) -> None:
    # Given a composed candidate in one workspace and a member of another
    store = SqliteWorkspaceStore(tmp_path)
    owner = _client(tmp_path, store, "First")
    other = _client(tmp_path, store, "Second")
    approved = _caption_approved(owner)
    composed = CandidateResponse.model_validate_json(
        owner.post(f"/api/candidates/{approved.candidate_id}/generate-image").content
    )

    # When both workspaces request the image
    owned = owner.get(f"/api/candidates/{composed.candidate_id}/image")
    foreign = other.get(f"/api/candidates/{composed.candidate_id}/image")

    # Then only the owning workspace receives the bytes
    assert owned.status_code == 200
    assert owned.headers["content-type"] == "image/png"
    assert sha256(owned.content).hexdigest() == composed.image_sha256
    assert foreign.status_code == 404


def test_candidate_without_an_image_has_no_image_route(tmp_path: Path) -> None:
    # Given a candidate that has not reached the image stage
    store = SqliteWorkspaceStore(tmp_path)
    client = _client(tmp_path, store, "Trace team")
    created = CandidateResponse.model_validate_json(
        client.post("/api/candidates", json=_payload()).content
    )

    # When
    response = client.get(f"/api/candidates/{created.candidate_id}/image")

    # Then
    assert response.status_code == 404
    assert response.json() == {"detail": "candidate image not found"}

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from trace_capture.candidate_generation import (
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateFormatError,
    CandidateGenerationError,
)
from trace_capture.web.app import create_app
from trace_capture.workspace import (
    CandidateCreate,
    CandidateSource,
    CandidateStatus,
    ProvisionedMember,
    ProvisionedWorkspace,
    SqliteWorkspaceStore,
)

from trace_capture.web.schemas import CandidateResponse  # isort: skip

if TYPE_CHECKING:
    from pathlib import Path

    from trace_capture.workspace import CandidateRecord, WorkspaceId

_CANDIDATES = TypeAdapter(tuple[CandidateResponse, ...])


@dataclass(frozen=True, slots=True)
class FakeGenerator:
    store: SqliteWorkspaceStore | None = None
    failure: CandidateGenerationError | None = None

    def generate(self, workspace_id: WorkspaceId) -> tuple[CandidateRecord, ...]:
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


def _client(
    root: Path,
    store: SqliteWorkspaceStore,
    name: str,
    generator: FakeGenerator | None = None,
) -> TestClient:
    workspace = store.create_workspace(name)
    member = store.create_member(workspace.workspace.workspace_id, "Ada")
    app = create_app(
        root,
        session_secret=b"s" * 32,
        candidate_generator=FakeGenerator(store) if generator is None else generator,
    )
    client = TestClient(app, base_url="https://test")
    _login(client, workspace, member)
    return client


def _payload(caption: str = "시험 기간엔 잠금화면부터 바꾼다") -> dict[str, object]:
    return {
        "topic": "시험기간 일정 관리 — 잠금화면 데모로 공감 훅",
        "country": "JP",
        "caption": caption,
        "hypothesis": "1인칭 감탄이 저장률을 올린다",
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

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

from ads_booster.workspace import (
    LEGACY_CANDIDATE_TOPIC,
    CandidateAlreadyReviewedError,
    CandidateBackgroundSubject,
    CandidateCreate,
    CandidateId,
    CandidateImageInputs,
    CandidatePostingSlot,
    CandidateRecord,
    CandidateSource,
    CandidateStatus,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
    WorkspaceId,
)


@pytest.fixture
def store(tmp_path: Path) -> SqliteWorkspaceStore:
    return SqliteWorkspaceStore(tmp_path)


def _manual(workspace_id: WorkspaceId, caption: str = "오늘의 캡션") -> CandidateCreate:
    return CandidateCreate(
        workspace_id=workspace_id,
        source=CandidateSource.MANUAL,
        country="JP",
        topic="시험기간 일정 관리 — 잠금화면 데모",
        caption=caption,
        hypothesis="시험 기간 공감 문장이 저장률을 올린다",
        image_inputs=CandidateImageInputs.model_validate(
            {
                "trace_items": ("09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"),
                "device_time": "07:20",
                "background_intent": "늦은 밤 책상 위 스탠드 불빛이 보이는 실제 공부방",
                "language": "ko",
            }
        ),
        refs_used=("ref-a", "ref-b"),
        principles_applied=(1, 4),
        shooting_order="- 책상 위 아이폰\n- 형광펜 두 자루",
    )


def test_created_candidate_is_listed_newest_first_with_its_input(
    store: SqliteWorkspaceStore,
) -> None:
    # Given
    workspace = store.create_workspace("Trace team").workspace

    # When
    first = store.create_candidate(_manual(workspace.workspace_id, "첫 캡션"))
    second = store.create_candidate(
        CandidateCreate(
            workspace_id=workspace.workspace_id,
            source=CandidateSource.AUTO,
            country="KR",
            topic="새 학기 준비 — 위젯 소개",
            caption="두 번째 캡션",
            hypothesis="자동 생성 가설",
            image_inputs=CandidateImageInputs.model_validate(
                {
                    "trace_items": ("09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"),
                    "device_time": "07:20",
                    "background_intent": "늦은 밤 책상 위 스탠드 불빛이 보이는 실제 공부방",
                    "language": "ko",
                }
            ),
            ai_verdict="수정 후 통과 — 1인칭 감탄 문장 빠짐",
            image_path="assets/candidate.png",
        )
    )
    listed = store.list_candidates(workspace.workspace_id)

    # Then
    assert listed == (second, first)
    assert first.source is CandidateSource.MANUAL
    assert first.topic == "시험기간 일정 관리 — 잠금화면 데모"
    assert second.topic == "새 학기 준비 — 위젯 소개"
    assert first.status is CandidateStatus.AWAITING_REVIEW
    assert first.refs_used == ("ref-a", "ref-b")
    assert first.principles_applied == (1, 4)
    assert first.review_note is None
    assert second.refs_used == ()
    assert second.ai_verdict == "수정 후 통과 — 1인칭 감탄 문장 빠짐"
    assert second.image_path == "assets/candidate.png"
    assert store.get_candidate(workspace.workspace_id, first.candidate_id) == first


def test_caption_approval_records_status_note_and_new_revision(
    store: SqliteWorkspaceStore,
) -> None:
    # Given
    workspace = store.create_workspace("Trace team").workspace
    candidate = store.create_candidate(_manual(workspace.workspace_id))

    # When
    reviewed = store.review_candidate(
        workspace.workspace_id,
        candidate.candidate_id,
        accepted=True,
        note=None,
        expected_revision=candidate.revision,
    )

    # Then
    assert reviewed.status is CandidateStatus.CAPTION_APPROVED
    assert reviewed.review_note is None
    assert reviewed.revision == candidate.revision + 1
    assert reviewed.updated_at >= candidate.updated_at
    assert store.get_candidate(workspace.workspace_id, candidate.candidate_id) == reviewed


_LEGACY_CANDIDATES_TABLE = """
DROP TABLE candidates;
CREATE TABLE candidates (
    workspace_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    source TEXT NOT NULL,
    country TEXT NOT NULL,
    caption TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    refs_used_json TEXT NOT NULL,
    principles_applied_json TEXT NOT NULL,
    shooting_order TEXT NOT NULL,
    ai_verdict TEXT,
    image_path TEXT,
    status TEXT NOT NULL,
    review_note TEXT,
    revision INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, candidate_id)
);
"""


def test_candidates_stored_before_topic_and_the_journey_are_migrated(tmp_path: Path) -> None:
    # Given a workspace whose candidates table predates topic and the approval journey
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team").workspace
    connection = sqlite3.connect(store.database_path)
    try:
        _ = connection.executescript(_LEGACY_CANDIDATES_TABLE)
        _ = connection.execute(
            "INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                workspace.workspace_id,
                "legacy-candidate",
                "manual",
                "JP",
                "이전 캡션",
                "이전 가설",
                "[]",
                "[]",
                "",
                None,
                None,
                "accepted",
                None,
                2,
                1.0,
                2.0,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    # When the workspace database is opened again
    reopened = SqliteWorkspaceStore(tmp_path)

    # Then the legacy row gains a placeholder topic and the first journey stage
    migrated = reopened.get_candidate(workspace.workspace_id, CandidateId("legacy-candidate"))
    assert migrated.topic == LEGACY_CANDIDATE_TOPIC
    assert migrated.status is CandidateStatus.CAPTION_APPROVED
    assert migrated.caption == "이전 캡션"
    assert reopened.list_candidates(workspace.workspace_id) == (migrated,)


def test_migrations_leave_current_candidates_untouched(tmp_path: Path) -> None:
    # Given a candidate written by the current store
    store = SqliteWorkspaceStore(tmp_path)
    workspace = store.create_workspace("Trace team").workspace
    candidate = store.create_candidate(_manual(workspace.workspace_id))

    # When the workspace database is opened again
    reopened = SqliteWorkspaceStore(tmp_path)

    # Then re-running the idempotent migrations changes nothing
    assert reopened.get_candidate(workspace.workspace_id, candidate.candidate_id) == candidate


def test_topic_is_required_on_every_candidate() -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        _ = CandidateCreate(
            workspace_id=WorkspaceId("workspace-1"),
            source=CandidateSource.MANUAL,
            country="KR",
            topic="",
            caption="주제 없는 후보",
            hypothesis="가설",
            image_inputs=CandidateImageInputs.model_validate(
                {
                    "trace_items": ("09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"),
                    "device_time": "07:20",
                    "background_intent": "늦은 밤 책상 위 스탠드 불빛이 보이는 실제 공부방",
                    "language": "ko",
                }
            ),
        )


def test_rejection_keeps_the_reason_for_the_next_generation(store: SqliteWorkspaceStore) -> None:
    # Given
    workspace = store.create_workspace("Trace team").workspace
    candidate = store.create_candidate(_manual(workspace.workspace_id))

    # When
    reviewed = store.review_candidate(
        workspace.workspace_id,
        candidate.candidate_id,
        accepted=False,
        note="1인칭 감탄이 빠졌습니다",
        expected_revision=candidate.revision,
    )

    # Then
    assert reviewed.status is CandidateStatus.REJECTED
    assert reviewed.review_note == "1인칭 감탄이 빠졌습니다"


def test_stale_revision_review_is_rejected(store: SqliteWorkspaceStore) -> None:
    # Given
    workspace = store.create_workspace("Trace team").workspace
    candidate = store.create_candidate(_manual(workspace.workspace_id))
    stale_revision = candidate.revision + 1

    # When / Then
    with pytest.raises(RevisionConflictError):
        _ = store.review_candidate(
            workspace.workspace_id,
            candidate.candidate_id,
            accepted=True,
            note=None,
            expected_revision=stale_revision,
        )
    assert (
        store.get_candidate(workspace.workspace_id, candidate.candidate_id).status
        is CandidateStatus.AWAITING_REVIEW
    )


def test_second_review_of_the_same_candidate_is_rejected(store: SqliteWorkspaceStore) -> None:
    # Given
    workspace = store.create_workspace("Trace team").workspace
    candidate = store.create_candidate(_manual(workspace.workspace_id))
    reviewed = store.review_candidate(
        workspace.workspace_id,
        candidate.candidate_id,
        accepted=True,
        note=None,
        expected_revision=candidate.revision,
    )

    # When / Then
    with pytest.raises(CandidateAlreadyReviewedError):
        _ = store.review_candidate(
            workspace.workspace_id,
            candidate.candidate_id,
            accepted=False,
            note="다시 반려",
            expected_revision=reviewed.revision,
        )
    assert store.get_candidate(workspace.workspace_id, candidate.candidate_id) == reviewed


def test_candidates_are_invisible_and_unreviewable_from_another_workspace(
    store: SqliteWorkspaceStore,
) -> None:
    # Given
    first = store.create_workspace("First").workspace
    second = store.create_workspace("Second").workspace
    candidate = store.create_candidate(_manual(first.workspace_id))

    # When
    listed_for_second: tuple[CandidateRecord, ...] = store.list_candidates(second.workspace_id)

    # Then
    assert listed_for_second == ()
    with pytest.raises(ScopedRecordNotFoundError):
        _ = store.get_candidate(second.workspace_id, candidate.candidate_id)
    with pytest.raises(ScopedRecordNotFoundError):
        _ = store.review_candidate(
            second.workspace_id,
            candidate.candidate_id,
            accepted=True,
            note=None,
            expected_revision=candidate.revision,
        )
    assert (
        store.get_candidate(first.workspace_id, candidate.candidate_id).status
        is CandidateStatus.AWAITING_REVIEW
    )


# The 22 columns of the schema above, in order.
_INSERT_DEMO_ERA_CANDIDATE = "INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"  # noqa: E501
_DEMO_ERA_SCHEMA = """
CREATE TABLE workspaces (
    workspace_id TEXT PRIMARY KEY, name TEXT NOT NULL, code_hash TEXT NOT NULL,
    code_version INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE candidates (
    workspace_id TEXT NOT NULL, candidate_id TEXT NOT NULL, source TEXT NOT NULL,
    country TEXT NOT NULL, topic TEXT NOT NULL, persona_domain TEXT, caption TEXT NOT NULL,
    hypothesis TEXT NOT NULL, refs_used_json TEXT NOT NULL, principles_applied_json TEXT NOT NULL,
    shooting_order TEXT NOT NULL, image_inputs_json TEXT, ai_verdict TEXT, image_path TEXT,
    image_sha256 TEXT, generation_provenance_json TEXT, background_provenance_json TEXT,
    status TEXT NOT NULL, review_note TEXT, revision INTEGER NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, candidate_id));
"""


def test_a_database_written_before_the_agent_columns_still_opens_and_reads(tmp_path: Path) -> None:
    # Given a database written by the build that had persona domains but no posting slot,
    # no agent run id, and a candidate stored with the subject and mood pair
    image_inputs = {
        "trace_items": ["09:00 통계학 2교시", "13:00 스터디"],
        "device_time": "07:20",
        "background_subject": "sports_team",
        "background_mood": "야구장 조명 아래 관중석",
        "background_search_query": "김도영 직캠",
        "language": "ko",
    }
    connection = sqlite3.connect(tmp_path / "workspace.sqlite3")
    try:
        _ = connection.executescript(_DEMO_ERA_SCHEMA)
        _ = connection.execute(
            "INSERT INTO workspaces VALUES (?, ?, ?, ?, ?, ?)",
            ("w-1", "Trace team", "hash", 1, 1.0, 1.0),
        )
        _ = connection.execute(
            _INSERT_DEMO_ERA_CANDIDATE,
            (
                "w-1",
                "c-1",
                "auto",
                "KR",
                "기아 팬의 하루",
                "sports_fan",
                "캡션",
                "가설",
                json.dumps(["kr-001"]),
                json.dumps([1]),
                "입력_일정: ...",
                json.dumps(image_inputs, ensure_ascii=False),
                None,
                None,
                None,
                None,
                None,
                "awaiting_review",
                None,
                1,
                1.0,
                1.0,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    # When the current build opens it
    store = SqliteWorkspaceStore(tmp_path)
    record = store.get_candidate(WorkspaceId("w-1"), CandidateId("c-1"))

    # Then the row reads back whole, the new columns are added with their defaults, and the
    # background intent the native path needs is composed rather than demanded
    assert record.topic == "기아 팬의 하루"
    assert record.persona_domain is not None
    assert record.persona_domain.value == "sports_fan"
    assert record.posting_slot is CandidatePostingSlot.MANUAL
    assert record.agent_run_id is None
    assert record.image_inputs is not None
    assert record.image_inputs.background_subject is CandidateBackgroundSubject.SPORTS_TEAM
    assert record.image_inputs.background_search_query == "김도영 직캠"
    assert record.image_inputs.background_intent == "sports_team: 야구장 조명 아래 관중석"
    assert store.count_candidate_domains(WorkspaceId("w-1")) == {"sports_fan": 1}

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

from trace_capture.workspace import (
    LEGACY_CANDIDATE_TOPIC,
    CandidateAlreadyReviewedError,
    CandidateBackgroundSubject,
    CandidateContextDocument,
    CandidateCreate,
    CandidateGenerationProvenance,
    CandidateId,
    CandidateImageInputs,
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
        image_inputs=CandidateImageInputs(
            trace_items=("09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"),
            device_time="07:20",
            background_subject=CandidateBackgroundSubject.SCENERY,
            background_mood="늦은 밤 책상 위 스탠드 불빛",
            language="ko",
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
            image_inputs=CandidateImageInputs(
                trace_items=("09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"),
                device_time="07:20",
                background_subject=CandidateBackgroundSubject.SCENERY,
                background_mood="늦은 밤 책상 위 스탠드 불빛",
                language="ko",
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


def test_generation_provenance_survives_the_round_trip_and_stays_absent_for_manual(
    store: SqliteWorkspaceStore,
) -> None:
    # Given a workspace and the provenance one generation batch recorded
    workspace = store.create_workspace("Trace team").workspace
    provenance = CandidateGenerationProvenance(
        documents=(
            CandidateContextDocument(relative_path="core/PRINCIPLES-KR.md", size_bytes=8_806),
            CandidateContextDocument(relative_path="references/KR/INDEX.md", size_bytes=1_240),
        ),
        model="gpt-5.5",
        instruction_chars=41_238,
        generated_at=1_770_000_000.0,
    )

    # When one generated candidate and one manual candidate are stored
    generated = store.create_candidate(
        CandidateCreate(
            workspace_id=workspace.workspace_id,
            source=CandidateSource.AUTO,
            country="KR",
            topic="새 학기 준비 — 위젯 소개",
            caption="자동 생성 캡션",
            hypothesis="자동 생성 가설",
            image_inputs=CandidateImageInputs(
                trace_items=("09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"),
                device_time="07:20",
                background_subject=CandidateBackgroundSubject.SCENERY,
                background_mood="늦은 밤 책상 위 스탠드 불빛",
                language="ko",
            ),
            generation_provenance=provenance,
        )
    )
    manual = store.create_candidate(_manual(workspace.workspace_id))

    # Then only the generated candidate carries the recorded provenance, unchanged by SQLite
    assert generated.generation_provenance == provenance
    assert manual.generation_provenance is None
    reread = store.get_candidate(workspace.workspace_id, generated.candidate_id)
    assert reread.generation_provenance == provenance
    assert store.get_candidate(workspace.workspace_id, manual.candidate_id) == manual


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
    assert migrated.generation_provenance is None
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
            image_inputs=CandidateImageInputs(
                trace_items=("09:00 통계학 2교시", "13:00 스터디", "19:00 러닝"),
                device_time="07:20",
                background_subject=CandidateBackgroundSubject.SCENERY,
                background_mood="늦은 밤 책상 위 스탠드 불빛",
                language="ko",
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

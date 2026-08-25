from __future__ import annotations

import time
from typing import Final, NoReturn
from uuid import uuid4

from pydantic import TypeAdapter

from trace_capture.workspace.database import SqliteCursor, WorkspaceRepositoryBase
from trace_capture.workspace.errors import (
    CandidateAlreadyReviewedError,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    WorkspaceStoreCorruptionError,
)
from trace_capture.workspace.models import (
    CandidateCreate,
    CandidateId,
    CandidateRecord,
    CandidateSource,
    CandidateStatus,
    WorkspaceId,
)

type CandidateRow = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str | None,
    str | None,
    str,
    str | None,
    int,
    float,
    float,
]

_REFS_ADAPTER = TypeAdapter(tuple[str, ...])
_PRINCIPLES_ADAPTER = TypeAdapter(tuple[int, ...])
_CANDIDATE: Final = "candidate"
_SELECT_CANDIDATE: Final = """
SELECT workspace_id, candidate_id, source, country, topic, caption, hypothesis, refs_used_json,
       principles_applied_json, shooting_order, ai_verdict, image_path, status, review_note,
       revision, created_at, updated_at
FROM candidates
"""
_INSERT_CANDIDATE: Final = """
INSERT INTO candidates (
    workspace_id, candidate_id, source, country, topic, caption, hypothesis, refs_used_json,
    principles_applied_json, shooting_order, ai_verdict, image_path, status, review_note,
    revision, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)
"""
_NEWEST_FIRST: Final = " ORDER BY created_at DESC, candidate_id DESC"


class CandidateStore(WorkspaceRepositoryBase):
    def create_candidate(self, value: CandidateCreate) -> CandidateRecord:
        candidate_id = CandidateId(uuid4().hex)
        now = time.time()
        status = CandidateStatus.AWAITING_REVIEW
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                _INSERT_CANDIDATE,
                (
                    value.workspace_id,
                    candidate_id,
                    value.source,
                    value.country,
                    value.topic,
                    value.caption,
                    value.hypothesis,
                    _REFS_ADAPTER.dump_json(value.refs_used).decode(),
                    _PRINCIPLES_ADAPTER.dump_json(value.principles_applied).decode(),
                    value.shooting_order,
                    value.ai_verdict,
                    value.image_path,
                    status,
                    now,
                    now,
                ),
            )
        return CandidateRecord(
            workspace_id=value.workspace_id,
            candidate_id=candidate_id,
            source=value.source,
            country=value.country,
            topic=value.topic,
            caption=value.caption,
            hypothesis=value.hypothesis,
            refs_used=value.refs_used,
            principles_applied=value.principles_applied,
            shooting_order=value.shooting_order,
            ai_verdict=value.ai_verdict,
            image_path=value.image_path,
            status=status,
            review_note=None,
            revision=1,
            created_at=now,
            updated_at=now,
        )

    def get_candidate(
        self, workspace_id: WorkspaceId, candidate_id: CandidateId
    ) -> CandidateRecord:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                f"{_SELECT_CANDIDATE} WHERE workspace_id = ? AND candidate_id = ?",
                (workspace_id, candidate_id),
            )
            row = _fetch_candidate(cursor)
        if row is None:
            raise ScopedRecordNotFoundError(record_type=_CANDIDATE, record_id=candidate_id)
        return _candidate_from_row(row)

    def list_candidates(self, workspace_id: WorkspaceId) -> tuple[CandidateRecord, ...]:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                f"{_SELECT_CANDIDATE} WHERE workspace_id = ?{_NEWEST_FIRST}",
                (workspace_id,),
            )
            rows: list[CandidateRecord] = []
            while (row := _fetch_candidate(cursor)) is not None:
                rows.append(_candidate_from_row(row))
        return tuple(rows)

    def review_candidate(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
        *,
        accepted: bool,
        note: str | None,
        expected_revision: int,
    ) -> CandidateRecord:
        """Apply the stage-one caption decision; later journey stages have no writer yet."""
        now = time.time()
        status = CandidateStatus.CAPTION_APPROVED if accepted else CandidateStatus.REJECTED
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE candidates
                SET status = ?, review_note = ?, revision = revision + 1, updated_at = ?
                WHERE workspace_id = ? AND candidate_id = ? AND revision = ? AND status = ?
                """,
                (
                    status,
                    note,
                    now,
                    workspace_id,
                    candidate_id,
                    expected_revision,
                    CandidateStatus.AWAITING_REVIEW,
                ),
            )
            if result.rowcount != 1:
                cursor: SqliteCursor = connection.execute(
                    "SELECT status FROM candidates WHERE workspace_id = ? AND candidate_id = ?",
                    (workspace_id, candidate_id),
                )
                _raise_review_failure(cursor, candidate_id, expected_revision)
        return self.get_candidate(workspace_id, candidate_id)


def _raise_review_failure(
    cursor: SqliteCursor, candidate_id: CandidateId, expected_revision: int
) -> NoReturn:
    match cursor.fetchone():
        case None:
            raise ScopedRecordNotFoundError(record_type=_CANDIDATE, record_id=candidate_id)
        case (str() as status,) if status != CandidateStatus.AWAITING_REVIEW:
            raise CandidateAlreadyReviewedError(record_id=candidate_id, status=status)
        case (str(),):
            raise RevisionConflictError(
                record_type=_CANDIDATE,
                record_id=candidate_id,
                expected_revision=expected_revision,
            )
        case _:
            raise WorkspaceStoreCorruptionError(record_type=_CANDIDATE)


def _candidate_from_row(row: CandidateRow) -> CandidateRecord:
    return CandidateRecord(
        workspace_id=WorkspaceId(row[0]),
        candidate_id=CandidateId(row[1]),
        source=CandidateSource(row[2]),
        country=row[3],
        topic=row[4],
        caption=row[5],
        hypothesis=row[6],
        refs_used=_REFS_ADAPTER.validate_json(row[7]),
        principles_applied=_PRINCIPLES_ADAPTER.validate_json(row[8]),
        shooting_order=row[9],
        ai_verdict=row[10],
        image_path=row[11],
        status=CandidateStatus(row[12]),
        review_note=row[13],
        revision=row[14],
        created_at=row[15],
        updated_at=row[16],
    )


def _fetch_candidate(cursor: SqliteCursor) -> CandidateRow | None:
    match cursor.fetchone():
        case None:
            return None
        case (
            str() as workspace_id,
            str() as candidate_id,
            str() as source,
            str() as country,
            str() as topic,
            str() as caption,
            str() as hypothesis,
            str() as refs_used_json,
            str() as principles_applied_json,
            str() as shooting_order,
            (str() | None) as ai_verdict,
            (str() | None) as image_path,
            str() as status,
            (str() | None) as review_note,
            int() as revision,
            float() as created_at,
            float() as updated_at,
        ):
            return (
                workspace_id,
                candidate_id,
                source,
                country,
                topic,
                caption,
                hypothesis,
                refs_used_json,
                principles_applied_json,
                shooting_order,
                ai_verdict,
                image_path,
                status,
                review_note,
                revision,
                created_at,
                updated_at,
            )
        case _:
            raise WorkspaceStoreCorruptionError(record_type=_CANDIDATE)

from __future__ import annotations

import time
from typing import Final, NoReturn
from uuid import uuid4

from pydantic import TypeAdapter

from trace_capture.workspace.database import SqliteCursor, WorkspaceRepositoryBase
from trace_capture.workspace.errors import (
    CandidateAlreadyReviewedError,
    CandidateStateError,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    WorkspaceStoreCorruptionError,
)
from trace_capture.workspace.models import (
    CandidateCreate,
    CandidateGenerationProvenance,
    CandidateId,
    CandidateImageInputs,
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
    str | None,
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
_IMAGE_INPUTS_ADAPTER = TypeAdapter(CandidateImageInputs)
_PROVENANCE_ADAPTER = TypeAdapter(CandidateGenerationProvenance)
_CANDIDATE: Final = "candidate"
_SELECT_CANDIDATE: Final = """
SELECT workspace_id, candidate_id, source, country, topic, caption, hypothesis, refs_used_json,
       principles_applied_json, shooting_order, image_inputs_json, ai_verdict, image_path,
       image_sha256, generation_provenance_json, status, review_note, revision, created_at,
       updated_at
FROM candidates
"""
_INSERT_CANDIDATE: Final = """
INSERT INTO candidates (
    workspace_id, candidate_id, source, country, topic, caption, hypothesis, refs_used_json,
    principles_applied_json, shooting_order, image_inputs_json, ai_verdict, image_path,
    image_sha256, generation_provenance_json, status, review_note, revision, created_at,
    updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, 1, ?, ?)
"""
_NEWEST_FIRST: Final = " ORDER BY created_at DESC, candidate_id DESC"
_SELECT_STATUS: Final = "SELECT status FROM candidates WHERE workspace_id = ? AND candidate_id = ?"


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
                    _dump_image_inputs(value.image_inputs),
                    value.ai_verdict,
                    value.image_path,
                    _dump_provenance(value.generation_provenance),
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
            image_inputs=value.image_inputs,
            ai_verdict=value.ai_verdict,
            image_path=value.image_path,
            image_sha256=None,
            generation_provenance=value.generation_provenance,
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

    def attach_candidate_image(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
        *,
        image_path: str,
        image_sha256: str,
        expected_revision: int,
    ) -> CandidateRecord:
        """Record a composed image and move the candidate to the image review gate."""
        now = time.time()
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE candidates
                SET image_path = ?, image_sha256 = ?, status = ?, review_note = NULL,
                    revision = revision + 1, updated_at = ?
                WHERE workspace_id = ? AND candidate_id = ? AND revision = ? AND status = ?
                """,
                (
                    image_path,
                    image_sha256,
                    CandidateStatus.IMAGE_AWAITING_REVIEW,
                    now,
                    workspace_id,
                    candidate_id,
                    expected_revision,
                    CandidateStatus.CAPTION_APPROVED,
                ),
            )
            if result.rowcount != 1:
                cursor: SqliteCursor = connection.execute(
                    _SELECT_STATUS, (workspace_id, candidate_id)
                )
                _raise_transition_failure(
                    cursor,
                    candidate_id,
                    expected_revision=expected_revision,
                    required=CandidateStatus.CAPTION_APPROVED,
                )
        return self.get_candidate(workspace_id, candidate_id)

    def review_candidate_image(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
        *,
        accepted: bool,
        note: str | None,
        expected_revision: int,
    ) -> CandidateRecord:
        """Apply the stage-two image decision.

        Approving submits the candidate; rejecting keeps the note, drops the composed
        image, and returns the candidate to `CAPTION_APPROVED` so it can be composed again.
        """
        now = time.time()
        status = CandidateStatus.SUBMITTED if accepted else CandidateStatus.CAPTION_APPROVED
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE candidates
                SET status = ?, review_note = ?, image_path = CASE WHEN ? THEN image_path END,
                    image_sha256 = CASE WHEN ? THEN image_sha256 END,
                    revision = revision + 1, updated_at = ?
                WHERE workspace_id = ? AND candidate_id = ? AND revision = ? AND status = ?
                """,
                (
                    status,
                    note,
                    accepted,
                    accepted,
                    now,
                    workspace_id,
                    candidate_id,
                    expected_revision,
                    CandidateStatus.IMAGE_AWAITING_REVIEW,
                ),
            )
            if result.rowcount != 1:
                cursor: SqliteCursor = connection.execute(
                    _SELECT_STATUS, (workspace_id, candidate_id)
                )
                _raise_transition_failure(
                    cursor,
                    candidate_id,
                    expected_revision=expected_revision,
                    required=CandidateStatus.IMAGE_AWAITING_REVIEW,
                )
        return self.get_candidate(workspace_id, candidate_id)


def _raise_transition_failure(
    cursor: SqliteCursor,
    candidate_id: CandidateId,
    *,
    expected_revision: int,
    required: CandidateStatus,
) -> NoReturn:
    match cursor.fetchone():
        case None:
            raise ScopedRecordNotFoundError(record_type=_CANDIDATE, record_id=candidate_id)
        case (str() as status,) if status != required:
            raise CandidateStateError(record_id=candidate_id, status=status, required=required)
        case (str(),):
            raise RevisionConflictError(
                record_type=_CANDIDATE,
                record_id=candidate_id,
                expected_revision=expected_revision,
            )
        case _:
            raise WorkspaceStoreCorruptionError(record_type=_CANDIDATE)


def _dump_image_inputs(value: CandidateImageInputs | None) -> str | None:
    return None if value is None else _IMAGE_INPUTS_ADAPTER.dump_json(value).decode()


def _load_image_inputs(payload: str | None) -> CandidateImageInputs | None:
    return None if payload is None else _IMAGE_INPUTS_ADAPTER.validate_json(payload)


def _dump_provenance(value: CandidateGenerationProvenance | None) -> str | None:
    return None if value is None else _PROVENANCE_ADAPTER.dump_json(value).decode()


def _load_provenance(payload: str | None) -> CandidateGenerationProvenance | None:
    return None if payload is None else _PROVENANCE_ADAPTER.validate_json(payload)


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
        image_inputs=_load_image_inputs(row[10]),
        ai_verdict=row[11],
        image_path=row[12],
        image_sha256=row[13],
        generation_provenance=_load_provenance(row[14]),
        status=CandidateStatus(row[15]),
        review_note=row[16],
        revision=row[17],
        created_at=row[18],
        updated_at=row[19],
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
            (str() | None) as image_inputs_json,
            (str() | None) as ai_verdict,
            (str() | None) as image_path,
            (str() | None) as image_sha256,
            (str() | None) as generation_provenance_json,
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
                image_inputs_json,
                ai_verdict,
                image_path,
                image_sha256,
                generation_provenance_json,
                status,
                review_note,
                revision,
                created_at,
                updated_at,
            )
        case _:
            raise WorkspaceStoreCorruptionError(record_type=_CANDIDATE)

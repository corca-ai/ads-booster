from __future__ import annotations

import time
from typing import TYPE_CHECKING, NoReturn

from pydantic import TypeAdapter, ValidationError

from ads_booster.workspace.candidate_base_store import CandidateBaseStore
from ads_booster.workspace.candidate_codec import CANDIDATE_RECORD, SELECT_STATUS
from ads_booster.workspace.errors import (
    CandidateAlreadyReviewedError,
    CandidateStateError,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    WorkspaceStoreCorruptionError,
)
from ads_booster.workspace.models import (
    CandidateId,
    CandidateImageAttachment,
    CandidateRecord,
    CandidateStatus,
    WorkspaceId,
)

if TYPE_CHECKING:
    from ads_booster.workspace.database import SqliteCursor

_STATUS_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


class CandidateStore(CandidateBaseStore):
    def review_candidate(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
        *,
        accepted: bool,
        note: str | None,
        expected_revision: int,
    ) -> CandidateRecord:
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
                    SELECT_STATUS,
                    (workspace_id, candidate_id),
                )
                _raise_review_failure(cursor, candidate_id, expected_revision)
        return self.get_candidate(workspace_id, candidate_id)

    def attach_candidate_image(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
        attachment: CandidateImageAttachment,
    ) -> CandidateRecord:
        now = time.time()
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                """
                UPDATE candidates
                SET image_path = ?, image_sha256 = ?, agent_run_id = ?, status = ?,
                    review_note = NULL, revision = revision + 1, updated_at = ?
                WHERE workspace_id = ? AND candidate_id = ? AND revision = ? AND status = ?
                """,
                (
                    attachment.path,
                    attachment.sha256,
                    attachment.agent_run_id,
                    CandidateStatus.IMAGE_AWAITING_REVIEW,
                    now,
                    workspace_id,
                    candidate_id,
                    attachment.expected_revision,
                    CandidateStatus.CAPTION_APPROVED,
                ),
            )
            if result.rowcount != 1:
                cursor: SqliteCursor = connection.execute(
                    SELECT_STATUS,
                    (workspace_id, candidate_id),
                )
                _raise_transition_failure(
                    cursor,
                    candidate_id,
                    expected_revision=attachment.expected_revision,
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
                    SELECT_STATUS,
                    (workspace_id, candidate_id),
                )
                _raise_transition_failure(
                    cursor,
                    candidate_id,
                    expected_revision=expected_revision,
                    required=CandidateStatus.IMAGE_AWAITING_REVIEW,
                )
        return self.get_candidate(workspace_id, candidate_id)


def _read_status(cursor: SqliteCursor) -> str | None:
    try:
        row = _STATUS_ROW.validate_python(cursor.fetchone())
    except ValidationError as error:
        raise WorkspaceStoreCorruptionError(record_type=CANDIDATE_RECORD) from error
    return None if row is None else row[0]


def _raise_transition_failure(
    cursor: SqliteCursor,
    candidate_id: CandidateId,
    *,
    expected_revision: int,
    required: CandidateStatus,
) -> NoReturn:
    status = _read_status(cursor)
    if status is None:
        raise ScopedRecordNotFoundError(record_type=CANDIDATE_RECORD, record_id=candidate_id)
    if status != required:
        raise CandidateStateError(record_id=candidate_id, status=status, required=required)
    raise RevisionConflictError(
        record_type=CANDIDATE_RECORD,
        record_id=candidate_id,
        expected_revision=expected_revision,
    )


def _raise_review_failure(
    cursor: SqliteCursor,
    candidate_id: CandidateId,
    expected_revision: int,
) -> NoReturn:
    status = _read_status(cursor)
    if status is None:
        raise ScopedRecordNotFoundError(record_type=CANDIDATE_RECORD, record_id=candidate_id)
    if status != CandidateStatus.AWAITING_REVIEW:
        raise CandidateAlreadyReviewedError(record_id=candidate_id, status=status)
    raise RevisionConflictError(
        record_type=CANDIDATE_RECORD,
        record_id=candidate_id,
        expected_revision=expected_revision,
    )

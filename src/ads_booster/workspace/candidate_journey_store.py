from __future__ import annotations

import time
from typing import TYPE_CHECKING, NoReturn

from pydantic import TypeAdapter, ValidationError

from ads_booster.workspace.candidate_base_store import CandidateBaseStore
from ads_booster.workspace.candidate_codec import (
    CANDIDATE_RECORD,
    SELECT_STATUS,
    dump_background_provenance,
    load_persona_domain,
)
from ads_booster.workspace.errors import (
    CandidateAlreadyReviewedError,
    CandidateStateError,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    WorkspaceStoreCorruptionError,
)
from ads_booster.workspace.models import (
    CandidateHistoryEntry,
    CandidateId,
    CandidateImageAttachment,
    CandidateRecord,
    CandidateSource,
    CandidateStatus,
    WorkspaceId,
)

if TYPE_CHECKING:
    from ads_booster.workspace.database import SqliteCursor

_STATUS_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


class CandidateStore(CandidateBaseStore):
    def count_candidate_domains(self, workspace_id: WorkspaceId) -> dict[str, int]:
        """Count how many generated candidates each persona domain already has.

        Only AUTO rows are counted: coverage is a property of what the generator has been
        producing, and a workspace that hand-wrote ten manual candidates in one domain has
        not thereby exhausted it. Domains with no rows are simply absent from the result;
        the caller knows the full vocabulary and treats a missing key as zero.
        """
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                """
                SELECT persona_domain, COUNT(*) FROM candidates
                WHERE workspace_id = ? AND source = ? AND persona_domain IS NOT NULL
                GROUP BY persona_domain
                """,
                (workspace_id, CandidateSource.AUTO),
            )
            counts: dict[str, int] = {}
            for row in cursor.fetchall():
                match row:
                    case (str() as domain, int() as total):
                        counts[domain] = total
                    case _:
                        continue
        return counts

    def recent_candidate_history(
        self, workspace_id: WorkspaceId, limit: int
    ) -> tuple[CandidateHistoryEntry, ...]:
        """Return the newest generated candidates as (domain, topic), newest first."""
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                """
                SELECT persona_domain, topic FROM candidates
                WHERE workspace_id = ? AND source = ?
                ORDER BY created_at DESC, candidate_id DESC
                LIMIT ?
                """,
                (workspace_id, CandidateSource.AUTO, limit),
            )
            entries: list[CandidateHistoryEntry] = []
            for row in cursor.fetchall():
                match row:
                    case ((str() | None) as domain, str() as topic):
                        entries.append(
                            CandidateHistoryEntry(
                                persona_domain=load_persona_domain(domain),
                                topic=topic,
                            )
                        )
                    case _:
                        continue
        return tuple(entries)

    def delete_candidate(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> None:
        """Remove one candidate from its own workspace, whatever stage it had reached.

        Deletion is not a review outcome, so no revision is expected and no status is
        required: a reviewer removing a candidate has already decided, and a row that
        moved stages between the click and the call should still go.
        """
        with self._database.connect(write=True) as connection:
            result = connection.execute(
                "DELETE FROM candidates WHERE workspace_id = ? AND candidate_id = ?",
                (workspace_id, candidate_id),
            )
            if result.rowcount == 0:
                raise ScopedRecordNotFoundError(
                    record_type=CANDIDATE_RECORD,
                    record_id=candidate_id,
                )

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
                SET image_path = ?, image_sha256 = ?, agent_run_id = ?,
                    background_provenance_json = ?, status = ?,
                    review_note = NULL, revision = revision + 1, updated_at = ?
                WHERE workspace_id = ? AND candidate_id = ? AND revision = ? AND status = ?
                """,
                (
                    attachment.path,
                    attachment.sha256,
                    attachment.agent_run_id,
                    dump_background_provenance(attachment.background_provenance),
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
                    background_provenance_json =
                        CASE WHEN ? THEN background_provenance_json END,
                    revision = revision + 1, updated_at = ?
                WHERE workspace_id = ? AND candidate_id = ? AND revision = ? AND status = ?
                """,
                (
                    status,
                    note,
                    accepted,
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

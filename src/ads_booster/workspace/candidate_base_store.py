from __future__ import annotations

import time
from uuid import uuid4

from ads_booster.workspace.candidate_codec import (
    CANDIDATE_RECORD,
    INSERT_CANDIDATE,
    NEWEST_FIRST,
    SELECT_CANDIDATE,
    candidate_from_row,
    dump_image_inputs,
    dump_principles,
    dump_references,
    fetch_candidate,
)
from ads_booster.workspace.database import SqliteCursor, WorkspaceRepositoryBase
from ads_booster.workspace.errors import ScopedRecordNotFoundError
from ads_booster.workspace.models import (
    CandidateCreate,
    CandidateId,
    CandidateRecord,
    CandidateStatus,
    WorkspaceId,
)


class CandidateBaseStore(WorkspaceRepositoryBase):
    def create_candidate(self, value: CandidateCreate) -> CandidateRecord:
        candidate_id = CandidateId(uuid4().hex)
        now = time.time()
        status = CandidateStatus.AWAITING_REVIEW
        with self._database.connect(write=True) as connection:
            _ = connection.execute(
                INSERT_CANDIDATE,
                (
                    value.workspace_id,
                    candidate_id,
                    value.source,
                    value.country,
                    value.topic,
                    value.caption,
                    value.hypothesis,
                    dump_references(value.refs_used),
                    dump_principles(value.principles_applied),
                    value.shooting_order,
                    dump_image_inputs(value.image_inputs),
                    value.ai_verdict,
                    value.image_path,
                    value.posting_slot,
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
            posting_slot=value.posting_slot,
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
            agent_run_id=None,
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
                f"{SELECT_CANDIDATE} WHERE workspace_id = ? AND candidate_id = ?",
                (workspace_id, candidate_id),
            )
            row = fetch_candidate(cursor)
        if row is None:
            raise ScopedRecordNotFoundError(
                record_type=CANDIDATE_RECORD,
                record_id=candidate_id,
            )
        return candidate_from_row(row)

    def list_candidates(self, workspace_id: WorkspaceId) -> tuple[CandidateRecord, ...]:
        with self._database.connect() as connection:
            cursor: SqliteCursor = connection.execute(
                f"{SELECT_CANDIDATE} WHERE workspace_id = ?{NEWEST_FIRST}",
                (workspace_id,),
            )
            rows: list[CandidateRecord] = []
            while (row := fetch_candidate(cursor)) is not None:
                rows.append(candidate_from_row(row))
        return tuple(rows)

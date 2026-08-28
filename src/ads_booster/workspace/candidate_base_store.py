from __future__ import annotations

import time
from uuid import uuid4

from ads_booster.workspace.candidate_codec import (
    CANDIDATE_RECORD,
    INSERT_CANDIDATE,
    NEWEST_FIRST,
    SELECT_CANDIDATE,
    candidate_from_row,
    dump_generation_provenance,
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
    MarketingAccountId,
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
                    value.persona_domain,
                    value.caption,
                    value.hypothesis,
                    dump_references(value.refs_used),
                    dump_principles(value.principles_applied),
                    value.shooting_order,
                    dump_image_inputs(value.image_inputs),
                    value.ai_verdict,
                    value.image_path,
                    dump_generation_provenance(value.generation_provenance),
                    value.posting_slot,
                    status,
                    now,
                    now,
                    value.account_id,
                ),
            )
        return CandidateRecord(
            workspace_id=value.workspace_id,
            candidate_id=candidate_id,
            account_id=value.account_id,
            source=value.source,
            country=value.country,
            posting_slot=value.posting_slot,
            topic=value.topic,
            persona_domain=value.persona_domain,
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
            generation_provenance=value.generation_provenance,
            background_provenance=None,
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

    def list_candidates(
        self,
        workspace_id: WorkspaceId,
        *,
        account_id: MarketingAccountId | None = None,
    ) -> tuple[CandidateRecord, ...]:
        """List the workspace's candidates, or only the ones one account wrote.

        An account is a person with its own posting record, so its screens must not show
        another account's drafts. With no account the whole workspace is returned, which is
        what the pre-account rows and the workspace-wide batch still need.
        """
        with self._database.connect() as connection:
            scope = "" if account_id is None else " AND account_id = ?"
            query = f"{SELECT_CANDIDATE} WHERE workspace_id = ?{scope}{NEWEST_FIRST}"
            parameters = (workspace_id,) if account_id is None else (workspace_id, account_id)
            cursor: SqliteCursor = connection.execute(query, parameters)
            rows: list[CandidateRecord] = []
            while (row := fetch_candidate(cursor)) is not None:
                rows.append(candidate_from_row(row))
        return tuple(rows)

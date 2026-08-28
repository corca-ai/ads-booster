from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ads_booster.candidate_generation.errors import CandidateImageStageError
from ads_booster.workspace import (
    CandidateCreate,
    CandidateId,
    CandidateRecord,
    CandidateStateError,
    CandidateStatus,
    MarketingAccountId,
    MarketingAccountRecord,
    RevisionConflictError,
    SqliteWorkspaceStore,
    WorkspaceId,
)

if TYPE_CHECKING:
    from ads_booster.candidate_generation.ports import (
        CandidateGeneratorPort,
        CandidateImageRunnerPort,
        ImageReviewPort,
    )

_CANDIDATE_RECORD: Final = "candidate"
_CORE_RUN_MISSING: Final = "candidate has no Agent run"


@dataclass(frozen=True, slots=True)
class CandidateReviewDecision:
    accepted: bool
    note: str | None
    expected_revision: int
    at: float


@dataclass(frozen=True, slots=True)
class CandidateWorkflow:
    store: SqliteWorkspaceStore
    generator: CandidateGeneratorPort
    image_runner: CandidateImageRunnerPort
    image_review: ImageReviewPort

    def list(
        self,
        workspace_id: WorkspaceId,
        account_id: MarketingAccountId | None = None,
    ) -> tuple[CandidateRecord, ...]:
        return self.store.list_candidates(workspace_id, account_id=account_id)

    def create(self, value: CandidateCreate) -> CandidateRecord:
        return self.store.create_candidate(value)

    def delete(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> None:
        return self.store.delete_candidate(workspace_id, candidate_id)

    def generate(
        self,
        workspace_id: WorkspaceId,
        account: MarketingAccountRecord | None = None,
    ) -> tuple[CandidateRecord, ...]:
        return self.generator.generate(workspace_id, account=account)

    def review_caption(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
        decision: CandidateReviewDecision,
    ) -> CandidateRecord:
        return self.store.review_candidate(
            workspace_id,
            candidate_id,
            accepted=decision.accepted,
            note=decision.note,
            expected_revision=decision.expected_revision,
        )

    def generate_image(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
    ) -> CandidateRecord:
        return self.image_runner.generate(workspace_id, candidate_id)

    def review_image(
        self,
        workspace_id: WorkspaceId,
        candidate_id: CandidateId,
        decision: CandidateReviewDecision,
    ) -> CandidateRecord:
        current = self.store.get_candidate(workspace_id, candidate_id)
        if current.revision != decision.expected_revision:
            raise RevisionConflictError(
                record_type=_CANDIDATE_RECORD,
                record_id=candidate_id,
                expected_revision=decision.expected_revision,
            )
        if current.status is not CandidateStatus.IMAGE_AWAITING_REVIEW:
            raise CandidateStateError(
                record_id=candidate_id,
                status=current.status,
                required=CandidateStatus.IMAGE_AWAITING_REVIEW,
            )
        if current.agent_run_id is None:
            raise CandidateImageStageError(_CORE_RUN_MISSING)
        self.image_review.review(
            current.agent_run_id,
            accepted=decision.accepted,
            note=decision.note,
            at=decision.at,
        )
        return self.store.review_candidate_image(
            workspace_id,
            candidate_id,
            accepted=decision.accepted,
            note=decision.note,
            expected_revision=decision.expected_revision,
        )

    def get(self, workspace_id: WorkspaceId, candidate_id: CandidateId) -> CandidateRecord:
        return self.store.get_candidate(workspace_id, candidate_id)

import shutil
import time
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from ads_booster.candidate_generation import (
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateFormatError,
    CandidateImageStageError,
    CandidateProviderError,
    CandidateRunConflictError,
)
from ads_booster.candidate_generation.workflow import (
    CandidateReviewDecision,
    CandidateWorkflow,
)
from ads_booster.web.auth import CurrentPrincipal, Principal  # noqa: TC001
from ads_booster.web.schemas import (
    CandidateCreateRequest,
    CandidateImageReviewRequest,
    CandidateResponse,
    CandidateReviewRequest,
)
from ads_booster.workspace import (
    CandidateAlreadyReviewedError,
    CandidateCreate,
    CandidateId,
    CandidateRecord,
    CandidateSource,
    CandidateStateError,
    MarketingAccountId,
    MarketingAccountReader,
    MarketingAccountRecord,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    WorkspaceId,
)

_CANDIDATE_NOT_FOUND: Final = "candidate not found"
_ACCOUNT_NOT_FOUND: Final = "marketing account not found"
_WRONG_IMAGE_STAGE: Final = "candidate is not caption approved"
_NO_IMAGE_TO_REVIEW: Final = "candidate has no image awaiting review"
_IMAGE_REVISION_CONFLICT: Final = "candidate revision conflict"
_CORE_RUN_CONFLICT: Final = "candidate Agent run conflict"
_NO_IMAGE: Final = "candidate image not found"
_CANDIDATE_DIRECTORY: Final = "candidates"


def _response(record: CandidateRecord) -> CandidateResponse:
    return CandidateResponse.model_validate(record, from_attributes=True)


def _remove_artifacts(image_root: Path, candidate_id: CandidateId) -> None:
    """Best-effort removal of one candidate's artifact directory.

    The path is resolved and confirmed to sit under the image root before anything is
    removed, so a candidate id that is not what it claims to be cannot reach outside it.
    """
    root = image_root.resolve()
    directory = (root / _CANDIDATE_DIRECTORY / candidate_id).resolve()
    if not directory.is_relative_to(root) or not directory.is_dir():
        return
    shutil.rmtree(directory, ignore_errors=True)


@dataclass(frozen=True, slots=True)
class CandidateRouter:
    workflow: CandidateWorkflow
    accounts: MarketingAccountReader
    current_principal: CurrentPrincipal
    image_root: Path

    def _account(
        self,
        workspace_id: WorkspaceId,
        account_id: MarketingAccountId | None,
    ) -> MarketingAccountRecord | None:
        """Resolve the account a batch is written as, or nothing for a workspace-wide run."""
        if account_id is None:
            return None
        try:
            return self.accounts.get_account(workspace_id, account_id)
        except ScopedRecordNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _ACCOUNT_NOT_FOUND) from error

    def build(self) -> APIRouter:
        router = APIRouter(prefix="/api/candidates", tags=["candidates"])
        self._register_candidate_routes(router)
        self._register_candidate_delete(router)
        self._register_image_generation(router)
        self._register_image_review(router)
        self._register_image_read(router)
        return router

    def _register_candidate_routes(self, router: APIRouter) -> None:
        current_principal = self.current_principal

        @router.get("", response_model=list[CandidateResponse])
        def list_candidates(
            principal: Annotated[Principal, Depends(current_principal)],
            account_id: MarketingAccountId | None = None,
        ) -> list[CandidateResponse]:
            records = self.workflow.list(principal.workspace_id, account_id)
            return [_response(record) for record in records]

        @router.post("", response_model=CandidateResponse, status_code=status.HTTP_201_CREATED)
        def create_candidate(
            payload: CandidateCreateRequest,
            principal: Annotated[Principal, Depends(current_principal)],
        ) -> CandidateResponse:
            return _response(
                self.workflow.create(
                    CandidateCreate(
                        workspace_id=principal.workspace_id,
                        source=CandidateSource.MANUAL,
                        country=payload.country,
                        posting_slot=payload.posting_slot,
                        topic=payload.topic,
                        persona_domain=payload.persona_domain,
                        caption=payload.caption,
                        hypothesis=payload.hypothesis,
                        image_inputs=payload.image_inputs,
                        refs_used=payload.refs_used,
                        principles_applied=payload.principles_applied,
                        shooting_order=payload.shooting_order,
                    )
                )
            )

        @router.post(
            "/generate",
            response_model=list[CandidateResponse],
            status_code=status.HTTP_201_CREATED,
        )
        def generate_candidates(
            principal: Annotated[Principal, Depends(current_principal)],
            account_id: MarketingAccountId | None = None,
        ) -> list[CandidateResponse]:
            # Generating for an account is the normal path; the workspace-wide batch stays
            # for a surface with no account chosen yet.
            account = self._account(principal.workspace_id, account_id)
            try:
                records = self.workflow.generate(principal.workspace_id, account)
            except (CandidateContextMissingError, CandidateAuthRequiredError) as error:
                raise HTTPException(status.HTTP_409_CONFLICT, error.message) from error
            except (CandidateProviderError, CandidateFormatError) as error:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, error.message) from error
            return [_response(record) for record in records]

        @router.post("/{candidate_id}/review", response_model=CandidateResponse)
        def review_candidate(
            candidate_id: CandidateId,
            payload: CandidateReviewRequest,
            principal: Annotated[Principal, Depends(current_principal)],
        ) -> CandidateResponse:
            try:
                record = self.workflow.review_caption(
                    principal.workspace_id,
                    candidate_id,
                    CandidateReviewDecision(
                        accepted=payload.accepted,
                        note=payload.note,
                        expected_revision=payload.expected_revision,
                        at=time.time(),
                    ),
                )
            except ScopedRecordNotFoundError as error:
                raise HTTPException(status.HTTP_404_NOT_FOUND, _CANDIDATE_NOT_FOUND) from error
            except CandidateAlreadyReviewedError as error:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "candidate already reviewed",
                ) from error
            except RevisionConflictError as error:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "candidate revision conflict",
                ) from error
            return _response(record)

        _ = (create_candidate, generate_candidates, list_candidates, review_candidate)

    def _register_candidate_delete(self, router: APIRouter) -> None:
        current_principal = self.current_principal

        @router.delete("/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
        def delete_candidate(
            candidate_id: CandidateId,
            principal: Annotated[Principal, Depends(current_principal)],
        ) -> None:
            """Delete one candidate and the artifacts it composed, at any stage.

            The row is removed first: it is the record the reviewer asked to be rid of, and
            a leftover directory is a housekeeping problem rather than a reason to keep a
            candidate the reviewer already dismissed. No revision is expected, because
            deletion is not a review outcome another writer can race.
            """
            try:
                self.workflow.delete(principal.workspace_id, candidate_id)
            except ScopedRecordNotFoundError as error:
                raise HTTPException(status.HTTP_404_NOT_FOUND, _CANDIDATE_NOT_FOUND) from error
            _remove_artifacts(self.image_root, candidate_id)

        _ = delete_candidate

    def _register_image_generation(self, router: APIRouter) -> None:
        current_principal = self.current_principal

        @router.post(
            "/{candidate_id}/generate-image",
            response_model=CandidateResponse,
            status_code=status.HTTP_201_CREATED,
        )
        def generate_candidate_image(
            candidate_id: CandidateId,
            principal: Annotated[Principal, Depends(current_principal)],
        ) -> CandidateResponse:
            try:
                record = self.workflow.generate_image(principal.workspace_id, candidate_id)
            except ScopedRecordNotFoundError as error:
                raise HTTPException(status.HTTP_404_NOT_FOUND, _CANDIDATE_NOT_FOUND) from error
            except CandidateStateError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, _WRONG_IMAGE_STAGE) from error
            except RevisionConflictError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, _IMAGE_REVISION_CONFLICT) from error
            except CandidateImageStageError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, error.message) from error
            except CandidateRunConflictError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, _CORE_RUN_CONFLICT) from error
            return _response(record)

        _ = generate_candidate_image

    def _register_image_review(self, router: APIRouter) -> None:
        current_principal = self.current_principal

        @router.post("/{candidate_id}/review-image", response_model=CandidateResponse)
        def review_candidate_image(
            candidate_id: CandidateId,
            payload: CandidateImageReviewRequest,
            principal: Annotated[Principal, Depends(current_principal)],
        ) -> CandidateResponse:
            try:
                record = self.workflow.review_image(
                    principal.workspace_id,
                    candidate_id,
                    CandidateReviewDecision(
                        accepted=payload.accepted,
                        note=payload.note,
                        expected_revision=payload.expected_revision,
                        at=time.time(),
                    ),
                )
            except ScopedRecordNotFoundError as error:
                raise HTTPException(status.HTTP_404_NOT_FOUND, _CANDIDATE_NOT_FOUND) from error
            except CandidateStateError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, _NO_IMAGE_TO_REVIEW) from error
            except RevisionConflictError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, _IMAGE_REVISION_CONFLICT) from error
            except CandidateImageStageError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, error.message) from error
            except CandidateRunConflictError as error:
                raise HTTPException(status.HTTP_409_CONFLICT, _CORE_RUN_CONFLICT) from error
            return _response(record)

        _ = review_candidate_image

    def _register_image_read(self, router: APIRouter) -> None:
        current_principal = self.current_principal

        @router.get("/{candidate_id}/image", response_class=FileResponse)
        def read_candidate_image(
            candidate_id: CandidateId,
            principal: Annotated[Principal, Depends(current_principal)],
        ) -> FileResponse:
            try:
                record = self.workflow.get(principal.workspace_id, candidate_id)
            except ScopedRecordNotFoundError as error:
                raise HTTPException(status.HTTP_404_NOT_FOUND, _CANDIDATE_NOT_FOUND) from error
            if record.image_path is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_IMAGE)
            root = self.image_root.resolve()
            path = (root / record.image_path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_IMAGE)
            return FileResponse(path, media_type="image/png")

        _ = read_candidate_image

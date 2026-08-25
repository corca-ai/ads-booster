from pathlib import Path  # noqa: TC003
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from trace_capture.candidate_generation import (
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateFormatError,
    CandidateGeneratorPort,
    CandidateImageRunnerPort,
    CandidateImageStageError,
    CandidateProviderError,
)
from trace_capture.web.auth import CurrentPrincipal, Principal  # noqa: TC001
from trace_capture.web.schemas import (
    CandidateCreateRequest,
    CandidateImageReviewRequest,
    CandidateResponse,
    CandidateReviewRequest,
)
from trace_capture.workspace import (
    CandidateAlreadyReviewedError,
    CandidateCreate,
    CandidateId,
    CandidateRecord,
    CandidateSource,
    CandidateStateError,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
)

_CANDIDATE_NOT_FOUND: Final = "candidate not found"
_WRONG_IMAGE_STAGE: Final = "candidate is not caption approved"
_NO_IMAGE_TO_REVIEW: Final = "candidate has no image awaiting review"
_IMAGE_REVISION_CONFLICT: Final = "candidate revision conflict"
_NO_IMAGE: Final = "candidate image not found"


def _response(record: CandidateRecord) -> CandidateResponse:
    return CandidateResponse.model_validate(record, from_attributes=True)


def build_candidate_router(
    store: SqliteWorkspaceStore,
    current_principal: CurrentPrincipal,
    generator: CandidateGeneratorPort,
    image_runner: CandidateImageRunnerPort,
    image_root: Path,
) -> APIRouter:
    router = APIRouter(prefix="/api/candidates", tags=["candidates"])

    @router.get("", response_model=list[CandidateResponse])
    def list_candidates(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> list[CandidateResponse]:
        return [_response(record) for record in store.list_candidates(principal.workspace_id)]

    @router.post("", response_model=CandidateResponse, status_code=status.HTTP_201_CREATED)
    def create_candidate(
        payload: CandidateCreateRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> CandidateResponse:
        record = store.create_candidate(
            CandidateCreate(
                workspace_id=principal.workspace_id,
                source=CandidateSource.MANUAL,
                country=payload.country,
                topic=payload.topic,
                caption=payload.caption,
                hypothesis=payload.hypothesis,
                image_inputs=payload.image_inputs,
                refs_used=payload.refs_used,
                principles_applied=payload.principles_applied,
                shooting_order=payload.shooting_order,
            )
        )
        return _response(record)

    @router.post(
        "/generate",
        response_model=list[CandidateResponse],
        status_code=status.HTTP_201_CREATED,
    )
    def generate_candidates(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> list[CandidateResponse]:
        """Assemble the context documents into one provider call and store its candidates."""
        try:
            records = generator.generate(principal.workspace_id)
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
            record = store.review_candidate(
                principal.workspace_id,
                candidate_id,
                accepted=payload.accepted,
                note=payload.note,
                expected_revision=payload.expected_revision,
            )
        except ScopedRecordNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _CANDIDATE_NOT_FOUND) from error
        except CandidateAlreadyReviewedError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "candidate already reviewed") from error
        except RevisionConflictError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "candidate revision conflict") from error
        return _response(record)

    _register_image_routes(router, store, current_principal, image_runner, image_root)
    _ = (list_candidates, create_candidate, generate_candidates, review_candidate)
    return router


def _register_image_routes(
    router: APIRouter,
    store: SqliteWorkspaceStore,
    current_principal: CurrentPrincipal,
    image_runner: CandidateImageRunnerPort,
    image_root: Path,
) -> None:
    """Register the stage-two image routes on the candidate router."""

    @router.post(
        "/{candidate_id}/generate-image",
        response_model=CandidateResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def generate_candidate_image(
        candidate_id: CandidateId,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> CandidateResponse:
        """Compose one lock-screen image and move the candidate to the image review gate."""
        try:
            record = image_runner.generate(principal.workspace_id, candidate_id)
        except ScopedRecordNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _CANDIDATE_NOT_FOUND) from error
        except CandidateStateError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, _WRONG_IMAGE_STAGE) from error
        except RevisionConflictError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, _IMAGE_REVISION_CONFLICT) from error
        except CandidateImageStageError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, error.message) from error
        return _response(record)

    @router.post("/{candidate_id}/review-image", response_model=CandidateResponse)
    def review_candidate_image(
        candidate_id: CandidateId,
        payload: CandidateImageReviewRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> CandidateResponse:
        """Apply the stage-two image decision: submit the post or send it back for a new image."""
        try:
            record = store.review_candidate_image(
                principal.workspace_id,
                candidate_id,
                accepted=payload.accepted,
                note=payload.note,
                expected_revision=payload.expected_revision,
            )
        except ScopedRecordNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _CANDIDATE_NOT_FOUND) from error
        except CandidateStateError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, _NO_IMAGE_TO_REVIEW) from error
        except RevisionConflictError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, _IMAGE_REVISION_CONFLICT) from error
        return _response(record)

    @router.get("/{candidate_id}/image", response_class=FileResponse)
    def read_candidate_image(
        candidate_id: CandidateId,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> FileResponse:
        """Serve the composed image of one candidate to its own workspace."""
        try:
            record = store.get_candidate(principal.workspace_id, candidate_id)
        except ScopedRecordNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _CANDIDATE_NOT_FOUND) from error
        if record.image_path is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_IMAGE)
        root = image_root.resolve()
        path = (root / record.image_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_IMAGE)
        return FileResponse(path, media_type="image/png")

    _ = (generate_candidate_image, review_candidate_image, read_candidate_image)

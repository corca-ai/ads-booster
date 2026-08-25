from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from trace_capture.candidate_generation import (
    CandidateAuthRequiredError,
    CandidateContextMissingError,
    CandidateFormatError,
    CandidateGeneratorPort,
    CandidateProviderError,
)
from trace_capture.web.auth import CurrentPrincipal, Principal  # noqa: TC001
from trace_capture.web.schemas import (
    CandidateCreateRequest,
    CandidateResponse,
    CandidateReviewRequest,
)
from trace_capture.workspace import (
    CandidateAlreadyReviewedError,
    CandidateCreate,
    CandidateId,
    CandidateRecord,
    CandidateSource,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
)


def _response(record: CandidateRecord) -> CandidateResponse:
    return CandidateResponse.model_validate(record, from_attributes=True)


def build_candidate_router(
    store: SqliteWorkspaceStore,
    current_principal: CurrentPrincipal,
    generator: CandidateGeneratorPort,
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
            raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate not found") from error
        except CandidateAlreadyReviewedError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "candidate already reviewed") from error
        except RevisionConflictError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "candidate revision conflict") from error
        return _response(record)

    _ = (list_candidates, create_candidate, generate_candidates, review_candidate)
    return router

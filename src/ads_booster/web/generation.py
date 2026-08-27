from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ads_booster.automation import (
    AutomationQueue,
    DuplicateIdempotencyError,
    QueueRecord,
    QueueSubmission,
)
from ads_booster.web.auth import CurrentPrincipal, Principal  # noqa: TC001
from ads_booster.web.schemas import GenerationRequest  # noqa: TC001


def build_generation_router(
    queue: AutomationQueue,
    current_principal: CurrentPrincipal,
) -> APIRouter:
    router = APIRouter(prefix="/api/generation", tags=["generation"])

    @router.post("", response_model=QueueRecord, status_code=status.HTTP_201_CREATED)
    def start_generation(
        payload: GenerationRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> QueueRecord:
        try:
            return queue.enqueue(
                QueueSubmission(
                    workspace_id=principal.workspace_id,
                    idempotency_key=payload.bundle.request_id,
                    bundle=payload.bundle,
                    due_at=datetime.now(UTC),
                )
            )
        except DuplicateIdempotencyError as error:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "generation request already exists with different input",
            ) from error

    _ = start_generation
    return router

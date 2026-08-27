from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from ads_booster.agent.runs import (
    AgentReview,
    AgentRunId,
    AgentRunNotFoundError,
    AgentRunResumer,
    AgentRunRevisionError,
    AgentRunStore,
    AgentRunTransitionError,
)
from ads_booster.automation import (
    AutomationQueue,
    DuplicateIdempotencyError,
    QueueId,
    QueueNotFoundError,
    QueueRecord,
    QueueRevisionError,
    QueueState,
    QueueSubmission,
)
from ads_booster.web.auth import CurrentPrincipal, Principal  # noqa: TC001
from ads_booster.web.schemas import QueueEnqueueRequest, QueueReviewRequest  # noqa: TC001


def build_queue_router(
    queue: AutomationQueue,
    agent_runs: AgentRunStore,
    current_principal: CurrentPrincipal,
) -> APIRouter:
    router = APIRouter(prefix="/api/queue", tags=["queue"])

    @router.get("", response_model=list[QueueRecord])
    def list_queue(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> tuple[QueueRecord, ...]:
        return queue.list_workspace(principal.workspace_id)

    @router.post("", response_model=QueueRecord, status_code=status.HTTP_201_CREATED)
    def enqueue(
        payload: QueueEnqueueRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> QueueRecord:
        due_at = payload.due_at or datetime.now(UTC)
        try:
            return queue.enqueue(
                QueueSubmission(
                    workspace_id=principal.workspace_id,
                    idempotency_key=payload.idempotency_key,
                    bundle=payload.bundle,
                    due_at=due_at,
                    max_attempts=payload.max_attempts,
                )
            )
        except DuplicateIdempotencyError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "idempotency key conflict") from error

    @router.post("/{queue_id}/review", response_model=QueueRecord)
    def review(
        queue_id: QueueId,
        payload: QueueReviewRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> QueueRecord:
        now = datetime.now(UTC)
        try:
            current = queue.get(principal.workspace_id, queue_id)
        except QueueNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "queue record not found") from error
        if (
            current.state is not QueueState.REVIEW
            or current.revision != payload.expected_revision
            or current.run_id is None
        ):
            raise HTTPException(status.HTTP_409_CONFLICT, "queue revision conflict")
        try:
            agent_run = agent_runs.get(AgentRunId(current.run_id))
            _ = AgentRunResumer(agent_runs).review(
                agent_run.run_id,
                AgentReview(
                    expected_revision=agent_run.revision,
                    accepted=payload.accepted,
                    note=None,
                    at=now.timestamp(),
                ),
            )
        except (
            AgentRunNotFoundError,
            AgentRunRevisionError,
            AgentRunTransitionError,
        ) as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "queue revision conflict") from error
        try:
            return queue.review(
                queue_id,
                workspace_id=principal.workspace_id,
                accepted=payload.accepted,
                expected_revision=payload.expected_revision,
                now=now,
            )
        except QueueRevisionError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "queue revision conflict") from error

    _ = (list_queue, enqueue, review)
    return router

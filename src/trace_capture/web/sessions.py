from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from trace_capture.web.auth import CurrentPrincipal, Principal
from trace_capture.web.schemas import SessionResponse, SessionSummaryResponse
from trace_capture.workspace import (
    PrivateSessionId,
    PrivateSessionRecord,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
)


def _summary(record: PrivateSessionRecord) -> SessionSummaryResponse:
    return SessionSummaryResponse(
        session_id=record.session_id,
        title=record.title,
        revision=record.revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def build_session_router(
    store: SqliteWorkspaceStore, current_principal: CurrentPrincipal
) -> APIRouter:
    router = APIRouter(prefix="/api/sessions", tags=["sessions"])

    @router.get("", response_model=list[SessionSummaryResponse])
    def list_sessions(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> list[SessionSummaryResponse]:
        records = store.list_private_sessions(principal.workspace_id, principal.member_id)
        return [_summary(record) for record in records]

    @router.get("/{session_id}", response_model=SessionResponse)
    def get_session(
        session_id: PrivateSessionId,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> SessionResponse:
        try:
            record = store.get_private_session(
                principal.workspace_id, principal.member_id, session_id
            )
        except ScopedRecordNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found") from error
        return SessionResponse(
            workspace_id=record.workspace_id,
            member_id=record.member_id,
            session_id=record.session_id,
            title=record.title,
            history=record.history,
            revision=record.revision,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    _ = (list_sessions, get_session)
    return router

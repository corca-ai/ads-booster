from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from trace_capture.web.auth import CurrentPrincipal, Principal
from trace_capture.web.schemas import (
    ContextCreateRequest,
    ContextResponse,
    ContextUpdateRequest,
)
from trace_capture.workspace import (
    ContextCreate,
    ContextId,
    ContextRecord,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
)


def _response(record: ContextRecord) -> ContextResponse:
    return ContextResponse.model_validate(record, from_attributes=True)


def build_context_router(
    store: SqliteWorkspaceStore, current_principal: CurrentPrincipal
) -> APIRouter:
    router = APIRouter(tags=["contexts"])

    @router.get("/api/contexts", response_model=list[ContextResponse])
    @router.get("/api/context", response_model=list[ContextResponse], include_in_schema=False)
    def list_contexts(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> list[ContextResponse]:
        return [_response(record) for record in store.list_contexts(principal.workspace_id)]

    @router.post(
        "/api/contexts", response_model=ContextResponse, status_code=status.HTTP_201_CREATED
    )
    @router.post(
        "/api/context",
        response_model=ContextResponse,
        status_code=status.HTTP_201_CREATED,
        include_in_schema=False,
    )
    def create_context(
        payload: ContextCreateRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> ContextResponse:
        record = store.create_context(
            principal.workspace_id,
            ContextCreate(kind=payload.kind, title=payload.title, body=payload.body),
        )
        return _response(record)

    @router.get("/api/contexts/{context_id}", response_model=ContextResponse)
    def get_context(
        context_id: ContextId,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> ContextResponse:
        try:
            return _response(store.get_context(principal.workspace_id, context_id))
        except ScopedRecordNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "context not found") from error

    @router.put("/api/contexts/{context_id}", response_model=ContextResponse)
    def update_context(
        context_id: ContextId,
        payload: ContextUpdateRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> ContextResponse:
        value = ContextCreate(kind=payload.kind, title=payload.title, body=payload.body)
        try:
            record = store.update_context(
                principal.workspace_id,
                context_id,
                value,
                expected_revision=payload.expected_revision,
            )
        except ScopedRecordNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "context not found") from error
        except RevisionConflictError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "context revision conflict") from error
        return _response(record)

    @router.delete("/api/contexts/{context_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_context(
        context_id: ContextId,
        expected_revision: int,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> Response:
        try:
            store.delete_context(
                principal.workspace_id,
                context_id,
                expected_revision=expected_revision,
            )
        except ScopedRecordNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "context not found") from error
        except RevisionConflictError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, "context revision conflict") from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    _ = (list_contexts, create_context, get_context, update_context, delete_context)
    return router

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from trace_capture.web.schemas import EmptyRunListResponse

if TYPE_CHECKING:
    from trace_capture.web.auth import CurrentPrincipal


def build_run_router(current_principal: CurrentPrincipal) -> APIRouter:
    router = APIRouter(
        prefix="/api/runs",
        tags=["runs"],
        dependencies=[Depends(current_principal)],
    )

    @router.get("", response_model=EmptyRunListResponse)
    def list_runs() -> EmptyRunListResponse:
        return EmptyRunListResponse(root=())

    _ = list_runs
    return router

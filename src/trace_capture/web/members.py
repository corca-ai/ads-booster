from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status

from trace_capture.web.auth import CurrentPrincipal, Principal  # noqa: TC001
from trace_capture.web.schemas import (
    MemberInviteRequest,
    MemberInviteResponse,
)
from trace_capture.workspace import SqliteWorkspaceStore  # noqa: TC001

_ACCESS_ID_SEPARATOR: Final = "%"
_ADMIN_REQUIRED: Final = "administrator access required"


def build_member_router(
    store: SqliteWorkspaceStore,
    current_principal: CurrentPrincipal,
) -> APIRouter:
    router = APIRouter(prefix="/api/members", tags=["members"])

    @router.post(
        "/invite",
        response_model=MemberInviteResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def invite_member(
        payload: MemberInviteRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> MemberInviteResponse:
        if not principal.is_admin:
            raise HTTPException(status.HTTP_403_FORBIDDEN, _ADMIN_REQUIRED)
        provisioned = store.create_member(principal.workspace_id, payload.display_name)
        access_id = _ACCESS_ID_SEPARATOR.join(
            (
                principal.workspace_id,
                provisioned.member.member_id,
                provisioned.invite_code,
            )
        )
        return MemberInviteResponse(
            member_access_id=access_id,
            display_name=provisioned.member.display_name,
        )

    _ = invite_member
    return router

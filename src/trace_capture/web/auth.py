from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from trace_capture.web.schemas import AuthenticatedMemberResponse, LoginRequest
from trace_capture.web.session import InvalidSessionError, SessionClaims, SessionCodec
from trace_capture.workspace import (
    MemberId,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
    WorkspaceId,
)

_COOKIE_NAME: Final = "trace_session"
_INVALID_CREDENTIALS: Final = "invalid credentials"
_UNAUTHORIZED: Final = "authentication required"


@dataclass(frozen=True, slots=True)
class Principal:
    workspace_id: WorkspaceId
    member_id: MemberId


@dataclass(frozen=True, slots=True)
class CurrentPrincipal:
    store: SqliteWorkspaceStore
    codec: SessionCodec

    def __call__(
        self,
        token: Annotated[str | None, Cookie(alias=_COOKIE_NAME)] = None,
    ) -> Principal:
        if token is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, _UNAUTHORIZED)
        try:
            claims = self.codec.decode(token)
            workspace = self.store.get_workspace(claims.workspace_id)
            member = self.store.get_member(claims.workspace_id, claims.member_id)
        except (InvalidSessionError, ScopedRecordNotFoundError) as error:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, _UNAUTHORIZED) from error
        if (
            claims.workspace_code_version != workspace.code_version
            or claims.member_code_version != member.code_version
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, _UNAUTHORIZED)
        return Principal(workspace_id=claims.workspace_id, member_id=claims.member_id)


def build_auth_router(
    store: SqliteWorkspaceStore,
    codec: SessionCodec,
    *,
    session_ttl_seconds: int,
) -> APIRouter:
    router = APIRouter(tags=["auth"])
    current_principal = CurrentPrincipal(store, codec)

    @router.post("/api/auth/login", response_model=AuthenticatedMemberResponse)
    @router.post("/login", response_model=AuthenticatedMemberResponse, include_in_schema=False)
    def login(payload: LoginRequest, response: Response) -> AuthenticatedMemberResponse:
        workspace_valid = store.verify_workspace_code(payload.workspace_id, payload.workspace_code)
        member_valid = store.verify_member_code(
            payload.workspace_id, payload.member_id, payload.member_code
        )
        if not (workspace_valid and member_valid):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, _INVALID_CREDENTIALS)
        workspace = store.get_workspace(payload.workspace_id)
        member = store.get_member(payload.workspace_id, payload.member_id)
        token = codec.issue(
            SessionClaims(
                workspace_id=payload.workspace_id,
                member_id=payload.member_id,
                workspace_code_version=workspace.code_version,
                member_code_version=member.code_version,
                expires_at=codec.clock() + session_ttl_seconds,
            )
        )
        response.set_cookie(
            _COOKIE_NAME,
            token,
            max_age=session_ttl_seconds,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return AuthenticatedMemberResponse(
            workspace_id=workspace.workspace_id,
            workspace_name=workspace.name,
            member_id=member.member_id,
            display_name=member.display_name,
        )

    @router.get("/api/auth/session", response_model=AuthenticatedMemberResponse)
    def get_session(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> AuthenticatedMemberResponse:
        workspace = store.get_workspace(principal.workspace_id)
        member = store.get_member(principal.workspace_id, principal.member_id)
        return AuthenticatedMemberResponse(
            workspace_id=workspace.workspace_id,
            workspace_name=workspace.name,
            member_id=member.member_id,
            display_name=member.display_name,
        )

    @router.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(response: Response) -> None:
        response.delete_cookie(_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")

    _ = (login, get_session, logout)
    return router

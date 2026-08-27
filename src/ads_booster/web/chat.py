from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, status

from ads_booster.agent.tui_commands import COMMAND_DESCRIPTIONS
from ads_booster.web.chat_runtime import (
    ChatRequestContext,
    agent_response_for,
    approval_response,
    chat_http_error,
    command_response_for,
    load_session,
)
from ads_booster.web.schemas import (
    ChatApprovalDecisionRequest,
    ChatApprovalDecisionResponse,
    ChatApprovalResponse,
    ChatCommandResponse,
    ChatErrorEnvelope,
    ChatRequest,
    ChatResponse,
)

if TYPE_CHECKING:
    from ads_booster.web.auth import CurrentPrincipal, Principal
    from ads_booster.web.chat_factory import WebAgentSessionFactory
    from ads_booster.workspace import SqliteWorkspaceStore


def build_chat_router(
    store: SqliteWorkspaceStore,
    current_principal: CurrentPrincipal,
    session_factory: WebAgentSessionFactory,
) -> APIRouter:
    router = APIRouter(prefix="/api/chat", tags=["chat"])

    @router.get("/commands", response_model=tuple[ChatCommandResponse, ...])
    def commands(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> tuple[ChatCommandResponse, ...]:
        _ = principal
        return tuple(
            ChatCommandResponse(command=command, description=description)
            for command, description in COMMAND_DESCRIPTIONS.items()
        )

    @router.post(
        "",
        response_model=ChatResponse,
        responses={
            status.HTTP_404_NOT_FOUND: {"model": ChatErrorEnvelope},
            status.HTTP_409_CONFLICT: {"model": ChatErrorEnvelope},
            status.HTTP_502_BAD_GATEWAY: {"model": ChatErrorEnvelope},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ChatErrorEnvelope},
        },
    )
    def send_message(
        payload: ChatRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> ChatResponse:
        context = ChatRequestContext(
            store=store,
            session_factory=session_factory,
            principal=principal,
            payload=payload,
            record=load_session(
                store,
                principal.workspace_id,
                principal.member_id,
                payload.session_id,
            ),
            prompt=payload.prompt.strip(),
        )
        if context.prompt.startswith("/"):
            return command_response_for(context)
        return agent_response_for(context)

    @router.get("/approval", response_model=ChatApprovalResponse | None)
    def pending_approval(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> ChatApprovalResponse | None:
        pending = session_factory.pending_approval(principal.workspace_id, principal.member_id)
        return approval_response(pending)

    @router.post(
        "/approval",
        response_model=ChatApprovalDecisionResponse,
        responses={status.HTTP_409_CONFLICT: {"model": ChatErrorEnvelope}},
    )
    def resolve_approval(
        payload: ChatApprovalDecisionRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> ChatApprovalDecisionResponse:
        resolved = session_factory.resolve_approval(
            principal.workspace_id,
            principal.member_id,
            payload.request_id,
            decision=payload.decision == "approve",
        )
        if not resolved:
            raise chat_http_error(
                status.HTTP_409_CONFLICT,
                "approval_not_found",
                "approval request is no longer pending",
            )
        return ChatApprovalDecisionResponse(resolved=True)

    _ = (commands, send_message, pending_approval, resolve_approval)
    return router

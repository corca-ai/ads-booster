from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from trace_capture.agent.session import AgentError
from trace_capture.auth.codex import OAuthError
from trace_capture.auth.store import AuthStoreError
from trace_capture.providers.errors import ProviderError
from trace_capture.web.chat_commands import WebCommandRequest, WebCommandResult
from trace_capture.web.schemas import (
    ChatApprovalResponse,
    ChatError,
    ChatRequest,
    ChatResponse,
    ChatSettingsResponse,
)
from trace_capture.workspace import (
    PrivateSessionCreate,
    PrivateSessionId,
    RevisionConflictError,
    ScopedRecordNotFoundError,
    SqliteWorkspaceStore,
)

if TYPE_CHECKING:
    from trace_capture.web.agent_state import PendingApproval, WebAgentStateSnapshot
    from trace_capture.web.auth import Principal
    from trace_capture.web.chat_factory import WebAgentSessionFactory
    from trace_capture.workspace import MemberId, PrivateSessionRecord, WorkspaceId


_SESSION_NOT_FOUND = "private session not found"


@dataclass(frozen=True, slots=True)
class ChatRequestContext:
    store: SqliteWorkspaceStore
    session_factory: WebAgentSessionFactory
    principal: Principal
    payload: ChatRequest
    record: PrivateSessionRecord | None
    prompt: str


def load_session(
    store: SqliteWorkspaceStore,
    workspace_id: WorkspaceId,
    member_id: MemberId,
    session_id: PrivateSessionId | None,
) -> PrivateSessionRecord | None:
    if session_id is None:
        return None
    try:
        return store.get_private_session(workspace_id, member_id, session_id)
    except ScopedRecordNotFoundError as error:
        raise chat_http_error(
            status.HTTP_404_NOT_FOUND,
            "session_not_found",
            _SESSION_NOT_FOUND,
        ) from error


def command_response_for(context: ChatRequestContext) -> ChatResponse:
    record = context.record
    command = context.session_factory.command(
        WebCommandRequest(
            store=context.store,
            workspace_id=context.principal.workspace_id,
            member_id=context.principal.member_id,
            session_id=context.payload.session_id,
            history=() if record is None else record.history,
            prompt=context.prompt,
        )
    )
    return command_response(command)


def agent_response_for(context: ChatRequestContext) -> ChatResponse:
    record = context.record
    history = () if record is None else record.history
    contexts = context.store.list_contexts(context.principal.workspace_id)
    try:
        turn = context.session_factory.run(
            context.principal.workspace_id,
            context.principal.member_id,
            history,
            contexts,
            context.prompt,
        )
    except OAuthError as error:
        raise chat_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication_required",
            "model provider authentication is required",
        ) from error
    except AuthStoreError as error:
        raise chat_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "authentication_unavailable",
            "model provider authentication is unavailable",
        ) from error
    except ProviderError as error:
        raise chat_http_error(
            status.HTTP_502_BAD_GATEWAY,
            error.code,
            "model provider request failed",
        ) from error
    except AgentError as error:
        raise chat_http_error(
            status.HTTP_502_BAD_GATEWAY,
            "agent_loop_failed",
            "agent tool loop failed",
        ) from error

    try:
        if record is None:
            saved = context.store.create_private_session(
                context.principal.workspace_id,
                context.principal.member_id,
                PrivateSessionCreate(title=context.prompt[:80], history=turn.private_history),
            )
        else:
            saved = context.store.update_private_session(
                context.principal.workspace_id,
                context.principal.member_id,
                record.session_id,
                expected_revision=record.revision,
                history=turn.private_history,
            )
    except RevisionConflictError as error:
        raise chat_http_error(
            status.HTTP_409_CONFLICT,
            "session_conflict",
            "private session changed during this request",
        ) from error
    return ChatResponse(
        session_id=saved.session_id,
        answer=turn.answer,
        history=saved.history,
        revision=saved.revision,
        settings=settings_response(
            context.session_factory.settings_for(
                context.principal.workspace_id,
                context.principal.member_id,
            )
        ),
    )


def chat_http_error(status_code: int, code: str, message: str) -> HTTPException:
    detail = ChatError(code=code, message=message).model_dump(mode="json")
    return HTTPException(status_code=status_code, detail=detail)


def command_response(result: WebCommandResult) -> ChatResponse:
    return ChatResponse(
        session_id=result.session_id,
        history=result.history,
        revision=result.revision,
        replace_history=result.replace_history,
        events=result.events,
        sessions=result.sessions,
        models=result.models,
        settings=settings_response(result.settings),
    )


def settings_response(snapshot: WebAgentStateSnapshot) -> ChatSettingsResponse:
    return ChatSettingsResponse(
        model=snapshot.model,
        reasoning=snapshot.reasoning,
        permission_mode=snapshot.permission_mode,
    )


def approval_response(pending: PendingApproval | None) -> ChatApprovalResponse | None:
    if pending is None:
        return None
    return ChatApprovalResponse(
        request_id=pending.request_id,
        action=pending.action,
        detail=pending.detail,
    )

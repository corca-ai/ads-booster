from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Final

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from trace_capture.automation import AutomationQueue, CampaignStore
from trace_capture.candidate_generation import (
    CandidateGeneratorPort,
    CandidateImageRunnerPort,
    build_candidate_generator,
    build_candidate_image_runner,
)
from trace_capture.config.settings import AgentSettings
from trace_capture.service.state import ServiceStateStore
from trace_capture.web.assets import build_asset_router
from trace_capture.web.auth import CurrentPrincipal, OwnerIdentity, build_auth_router
from trace_capture.web.campaigns import build_campaign_router
from trace_capture.web.candidates import build_candidate_router
from trace_capture.web.chat import build_chat_router
from trace_capture.web.chat_factory import WebAgentSessionFactory
from trace_capture.web.contexts import build_context_router
from trace_capture.web.generation import build_generation_router
from trace_capture.web.members import build_member_router
from trace_capture.web.queue import build_queue_router
from trace_capture.web.runs import build_run_router
from trace_capture.web.schemas import HealthResponse
from trace_capture.web.session import Clock, SessionCodec
from trace_capture.web.sessions import build_session_router
from trace_capture.workspace import SqliteWorkspaceStore

_DEFAULT_SESSION_TTL_SECONDS: Final = 8 * 60 * 60
_STATIC_ROOT: Final = Path(__file__).with_name("static")


def create_app(
    root: Path | None = None,
    *,
    session_secret: bytes | None = None,
    session_ttl_seconds: int = _DEFAULT_SESSION_TTL_SECONDS,
    clock: Clock = time.time,
    chat_factory: WebAgentSessionFactory | None = None,
    candidate_generator: CandidateGeneratorPort | None = None,
    candidate_image_runner: CandidateImageRunnerPort | None = None,
) -> FastAPI:
    store = SqliteWorkspaceStore(root)
    state = ServiceStateStore(store.database_path.parent).load()
    owner_identity = (
        None
        if state is None
        else OwnerIdentity(workspace_id=state.workspace_id, member_id=state.member_id)
    )
    secret = secrets.token_bytes(32) if session_secret is None else session_secret
    codec = SessionCodec(secret=secret, clock=clock)
    current_principal = CurrentPrincipal(store, codec, owner_identity)
    settings = AgentSettings.from_environment()
    active_chat_factory = (
        WebAgentSessionFactory.production(settings) if chat_factory is None else chat_factory
    )
    home = store.database_path.parent
    active_generator = (
        build_candidate_generator(settings, store)
        if candidate_generator is None
        else candidate_generator
    )
    active_image_runner = (
        build_candidate_image_runner(settings, home, store)
        if candidate_image_runner is None
        else candidate_image_runner
    )
    app = FastAPI(title="Trace Workspace API")
    app.include_router(
        build_auth_router(
            store,
            codec,
            session_ttl_seconds=session_ttl_seconds,
            owner_identity=owner_identity,
        )
    )
    app.include_router(build_context_router(store, current_principal))
    app.include_router(build_asset_router(store, current_principal))
    app.include_router(
        build_candidate_router(
            store,
            current_principal,
            active_generator,
            active_image_runner,
            home,
        )
    )
    app.include_router(build_session_router(store, current_principal))
    app.include_router(build_chat_router(store, current_principal, active_chat_factory))
    app.include_router(build_run_router(current_principal))
    app.include_router(
        build_campaign_router(
            store.database_path.parent,
            store,
            CampaignStore(store.database_path.parent),
            current_principal,
        )
    )
    app.include_router(
        build_generation_router(AutomationQueue(store.database_path.parent), current_principal)
    )
    app.include_router(build_member_router(store, current_principal))
    app.include_router(
        build_queue_router(AutomationQueue(store.database_path.parent), current_principal)
    )
    app.mount("/static", StaticFiles(directory=_STATIC_ROOT), name="static")

    @app.get("/", response_class=FileResponse, include_in_schema=False)
    def workspace_shell() -> FileResponse:
        return FileResponse(_STATIC_ROOT / "workspace.html", media_type="text/html")

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    _ = (health, workspace_shell)
    return app

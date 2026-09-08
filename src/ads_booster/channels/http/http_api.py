"""Small authenticated HTTP boundary for the on-premises Marketing Agent Service."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Literal, override
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentGoal,
    AgentRecordKind,
    ToolInvocation,
    contract_sha256,
)
from ads_booster.contracts.knowledge_context import ContextTransferValidationRequest
from ads_booster.contracts.models import ContractModel
from ads_booster.knowledge.errors import KnowledgePolicyError
from ads_booster.agent.service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.channels.http.browser_login import BrowserLogin
from ads_booster.channels.http.creative_api import dispatch_creative
from ads_booster.channels.http.delivery_api import dispatch_delivery
from ads_booster.channels.http.image_edit_api import dispatch_image_edit
from ads_booster.channels.http.jobs import AgentJobs, WebJob
from ads_booster.agent.service.knowledge_ingress import (
    CanonicalKnowledgeIngress,
    PendingKnowledgeIngress,
)
from ads_booster.channels.http.knowledge_ingress_api import (
    ApiIngressRequest,
    build_api_ingress,
)
from ads_booster.agent.service.knowledge_transfer import KnowledgeTransferProvider
from ads_booster.agent.service.maintenance import MaintenanceGate
from ads_booster.learning.memory import SQLiteMemoryStore
from ads_booster.channels.http.memory_api import dispatch_memory
from ads_booster.channels.http.oauth import AccessTokenAuthenticator, OAuthIdentity
from ads_booster.channels.http.performance_api import dispatch_performance
from ads_booster.agent.service.skills import MarketingSkillCatalog
from ads_booster.agent.service.sqlite_repository import RepositoryAdmission
from ads_booster.channels.http.web_ui import AGENT_RUN_UI
from ads_booster.agent.service.work_continuation import continue_work
from ads_booster.channels.slack_commands import SlackCommands
from ads_booster.channels.slack_conversations import SlackInboxFullError
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.providers.codex_reasoning import CodexReasoningError
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

_MAX_BODY_BYTES = 1024 * 1024
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ApiCreateRunRequest(ContractModel):
    request_id: str | None = None
    run_id: str
    goal: AgentGoal
    budget: AgentBudget


class ApiContinuationRequest(ContractModel):
    event_id: str
    note: str
    action: Literal["revise", "pause"]


class ApiInputRequest(ContractModel):
    request_id: str | None = None
    corrects_revision_ref: str | None = None
    evidence: JsonObject


class ApiApprovalRequest(ContractModel):
    invocation_sha256: str
    decision: Literal["granted", "rejected"]
    expires_at: datetime | None = None


class ApiSkillRunRequest(ContractModel):
    run_id: str
    context: JsonObject
    budget: AgentBudget = Field(
        default_factory=lambda: AgentBudget(max_tool_calls=6, max_cost_units=100)
    )


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status: int
    body: JsonObject | str
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class MarketingAgentApi:
    service: MarketingAgentService
    tenant_id: str
    principal_id: str
    bearer_token: str
    oauth_authenticator: AccessTokenAuthenticator | None = None
    browser_login: BrowserLogin | None = None
    slack_commands: SlackCommands | None = None
    slack_events: SlackEvents | None = None
    jobs: AgentJobs | None = None
    allowed_tenant_id: str | None = None
    slack_only: bool = False
    maintenance: MaintenanceGate | None = None
    approval_authorizer: Callable[[OAuthIdentity], bool] | None = None

    knowledge_ingress: CanonicalKnowledgeIngress | None = None
    knowledge_transfers: KnowledgeTransferProvider | None = None

    def __post_init__(self) -> None:
        """Install current approval authority and canonical ingress on the same Run ledger."""
        if self.jobs is not None:
            self.jobs.approval_authorizer = self._can_approve
        if self.knowledge_ingress is None:
            object.__setattr__(
                self,
                "knowledge_ingress",
                CanonicalKnowledgeIngress(self.service.repository.database_path)
                if self.service.knowledge is None
                else self.service.knowledge.ingress,
            )

    def _knowledge_admission(
        self, ingress: PendingKnowledgeIngress | None
    ) -> RepositoryAdmission | None:
        owner = self.knowledge_ingress
        if owner is None or ingress is None:
            return None

        def admit(connection: sqlite3.Connection) -> None:
            _ = owner.admit(connection, ingress.binding, ingress.event, ingress.envelope)

        return admit

    def dispatch(  # noqa: PLR0913 - preserve the HTTP boundary call contract.
        self,
        method: str,
        target: str,
        *,
        authorization: str | None,
        body: bytes = b"",
        now: datetime | None = None,
        headers: dict[str, str] | None = None,
    ) -> ApiResponse:
        if method == "GET" and urlsplit(target).path == "/health":
            health: JsonObject = {"status": "ok", "owner": "on_prem_agent"}
            if self.maintenance is not None:
                health.update(self.maintenance.health())
            return ApiResponse(200, health)
        # Cancellation admits no new work and remains available while an update drains.
        if method == "POST" and urlsplit(target).path == "/channels/slack/interactions":
            return self._dispatch(
                method, target, authorization=authorization, body=body, now=now, headers=headers
            )
        if self.maintenance is not None:
            with self.maintenance.work() as admitted:
                if not admitted:
                    return ApiResponse(503, {"error": "agent_updating"})
                return self._dispatch(
                    method, target, authorization=authorization, body=body, now=now, headers=headers
                )
        return self._dispatch(
            method, target, authorization=authorization, body=body, now=now, headers=headers
        )

    def _dispatch(  # noqa: C901,PLR0911,PLR0912,PLR0913,PLR0915 - explicit authenticated routes.
        self,
        method: str,
        target: str,
        *,
        authorization: str | None,
        body: bytes = b"",
        now: datetime | None = None,
        headers: dict[str, str] | None = None,
    ) -> ApiResponse:
        path = urlsplit(target).path
        headers = headers or {}
        if (
            method == "POST"
            and path == "/channels/slack/commands"
            and self.slack_commands is not None
        ):
            try:
                return ApiResponse(
                    200, self.slack_commands.receive(body, headers, now=now or datetime.now(UTC))
                )
            except ValueError, UnicodeError:
                return ApiResponse(403, {"error": "slack_command_rejected"})
        if method == "POST" and path == "/channels/slack/events" and self.slack_events is not None:
            try:
                return ApiResponse(
                    200, self.slack_events.receive(body, headers, now=now or datetime.now(UTC))
                )
            except SlackInboxFullError:
                return ApiResponse(503, {"error": "slack_inbox_full"})
            except ValueError, UnicodeError:
                return ApiResponse(403, {"error": "slack_event_rejected"})
        if (
            method == "POST"
            and path == "/channels/slack/interactions"
            and self.slack_events is not None
        ):
            try:
                return ApiResponse(
                    200, self.slack_events.interact(body, headers, now=now or datetime.now(UTC))
                )
            except ValueError, UnicodeError:
                return ApiResponse(403, {"error": "slack_interaction_rejected"})
        if self.slack_only:
            return ApiResponse(404, {"error": "slack_only_service"})
        login = self.browser_login
        if method == "GET" and path == "/auth/config":
            return ApiResponse(200, {"browser_login": login is not None})
        if login is not None and method == "GET" and path in {"/auth/login", "/auth/callback"}:
            try:
                if path == "/auth/login":
                    location, cookie = login.start()
                    return ApiResponse(
                        303, "", headers=(("location", location), ("set-cookie", cookie))
                    )
                query = parse_qs(urlsplit(target).query)
                cookie = login.callback(
                    query.get("state", [""])[0], query.get("code", [""])[0], headers.get("cookie")
                )
                return ApiResponse(303, "", headers=(("location", "/"), ("set-cookie", cookie)))
            except ValueError:
                return ApiResponse(401, {"error": "agent_login_failed"})
        if method == "GET" and (path == "/" or _run_ui_path(path) is not None):
            return ApiResponse(HTTPStatus.OK, AGENT_RUN_UI, "text/html; charset=utf-8")
        if method == "GET" and path == "/health":
            return ApiResponse(HTTPStatus.OK, {"status": "ok", "owner": "on_prem_agent"})
        session = None if login is None else login.session(headers.get("cookie"))
        identity = self._identity(authorization)
        if authorization is None and session is not None and login is not None:
            identity = login.identity(session)
            if method != "GET" and not login.mutation_allowed(
                session, headers.get("origin"), headers.get("x-trace-csrf")
            ):
                return ApiResponse(403, {"error": "agent_csrf_rejected"})
        if identity is None:
            return ApiResponse(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        if self.allowed_tenant_id is not None and identity.tenant_id != self.allowed_tenant_id:
            return ApiResponse(403, {"error": "agent_workspace_not_allowed"})
        if method == "GET" and path == "/auth/session":
            return ApiResponse(
                200,
                {
                    "tenant_id": identity.tenant_id,
                    "principal_id": identity.principal_id,
                    "csrf": session.csrf if session is not None else "",
                },
            )
        if method == "POST" and path == "/auth/logout" and login is not None:
            return ApiResponse(
                200, {"ok": True}, headers=(("set-cookie", login.logout(headers.get("cookie"))),)
            )
        occurred_at = datetime.now(UTC) if now is None else now
        try:
            image_edit_response = dispatch_image_edit(
                method,
                path,
                body,
                identity=identity,
                service=self.service,
                review_authorizer=self._can_approve,
            )
            if image_edit_response is not None:
                return ApiResponse(*image_edit_response)
            performance_response = dispatch_performance(
                method, target, identity=identity, service=self.service
            )
            if performance_response is not None:
                return ApiResponse(*performance_response)
            creative_response = dispatch_creative(
                method,
                path,
                body,
                identity=identity,
                service=self.service,
                artifact_root=self.service.repository.database_path.parent / "artifacts",
                now=occurred_at,
            )
            if creative_response is not None:
                return ApiResponse(*creative_response)
            delivery_response = dispatch_delivery(
                method, path, body, identity=identity, service=self.service
            )
            if delivery_response is not None:
                return ApiResponse(*delivery_response)
            memory_response = dispatch_memory(
                method,
                target,
                body,
                identity=identity,
                store=SQLiteMemoryStore(self.service.repository.database_path),
                now=occurred_at,
            )
            if memory_response is not None:
                return ApiResponse(*memory_response)
            transfer_id = _context_transfer_validation_target(path)
            if method == "POST" and transfer_id is not None:
                if self.knowledge_transfers is None:
                    return ApiResponse(503, {"error": "knowledge_transfer_owner_unavailable"})
                request = ContextTransferValidationRequest.model_validate(_body_json(body))
                if request.transfer_id != transfer_id:
                    return ApiResponse(409, {"error": "knowledge_transfer_route_mismatch"})
                result = self.knowledge_transfers.validate_transfer(
                    request,
                    authenticated_tenant_id=identity.tenant_id,
                    authenticated_principal_id=identity.principal_id,
                )
                return ApiResponse(200, result.model_dump(mode="json", by_alias=True))
            if self.jobs is not None and method == "POST" and path == "/v1/jobs":
                job = WebJob.model_validate(_body_json(body))
                if job.action == "approval" and not self._can_approve(identity):
                    return ApiResponse(403, {"error": "agent_approval_permission_required"})
                return ApiResponse(
                    202,
                    self.jobs.enqueue(
                        identity.tenant_id,
                        identity.principal_id,
                        job,
                        now=occurred_at,
                    ),
                )
            if self.jobs is not None and method == "GET" and path.startswith("/v1/jobs/"):
                return ApiResponse(
                    200, self.jobs.status(identity.tenant_id, path.removeprefix("/v1/jobs/"))
                )
            skills = MarketingSkillCatalog(self.service.registry)
            if method == "GET" and path == "/v1/tools":
                return ApiResponse(
                    HTTPStatus.OK,
                    {
                        "tools": [
                            {
                                "capability_id": item.capability_id,
                                "version": item.version,
                                "owner": item.owner,
                                "effect_class": item.effect_class.value,
                                "approval_mode": item.approval_policy.mode,
                                "ready": item.readiness.ready,
                            }
                            for item in self.service.registry.current_descriptors(now=occurred_at)
                        ]
                    },
                )
            if method == "GET" and path == "/v1/skills":
                return ApiResponse(
                    HTTPStatus.OK,
                    _JSON_OBJECT.validate_python({"skills": skills.list(now=occurred_at)}),
                )
            skill_id = _skill_run_target(path)
            if method == "POST" and skill_id is not None:
                request = ApiSkillRunRequest.model_validate(_body_json(body))
                skill = skills.require_ready(skill_id, now=occurred_at)
                run = self.service.create(
                    CreateAgentRunRequest(
                        run_id=request.run_id,
                        tenant_id=identity.tenant_id,
                        goal=skill.goal(request.context),
                        budget=request.budget,
                    ),
                    now=occurred_at,
                )
                return ApiResponse(
                    HTTPStatus.ACCEPTED,
                    self._run_view(identity.tenant_id, run.run_id),
                )
            if method == "POST" and path == "/v1/runs":
                request = ApiCreateRunRequest.model_validate(_body_json(body))
                request_id = request.request_id or "api-request-" + contract_sha256(request)[:40]
                ingress = (
                    None
                    if self.knowledge_ingress is None
                    else build_api_ingress(
                        ApiIngressRequest(
                            request_id=request_id,
                            run_id=request.run_id,
                            action="create",
                            text=request.goal.objective,
                            identity=identity,
                            revision=1,
                            occurred_at=occurred_at,
                        )
                    )
                )
                run = self.service.create(
                    CreateAgentRunRequest(
                        run_id=request.run_id,
                        tenant_id=identity.tenant_id,
                        goal=request.goal,
                        budget=request.budget,
                    ),
                    now=occurred_at,
                    admission=self._knowledge_admission(ingress),
                )
                return ApiResponse(
                    HTTPStatus.ACCEPTED,
                    self._run_view(identity.tenant_id, run.run_id),
                )
            if method == "GET" and path == "/v1/runs":
                return ApiResponse(
                    HTTPStatus.OK,
                    {
                        "runs": [
                            item.model_dump(mode="json")
                            for item in self.service.repository.list_runs(identity.tenant_id)
                        ]
                    },
                )
            run_id, suffix = _run_target(path)
            run = self.service.repository.get(identity.tenant_id, run_id)
            if run is None:
                return ApiResponse(HTTPStatus.NOT_FOUND, {"error": "agent_run_not_found"})
            if method == "POST" and suffix == "/continuation":
                continuation = ApiContinuationRequest.model_validate(_body_json(body))
                _ = continue_work(
                    self.service,
                    identity.tenant_id,
                    run_id,
                    event_id=continuation.event_id,
                    actor_id=identity.principal_id,
                    note=continuation.note,
                    action=continuation.action,
                    now=occurred_at,
                )
                return ApiResponse(200, self._run_view(identity.tenant_id, run_id))
            if method == "GET" and suffix == "":
                return ApiResponse(HTTPStatus.OK, self._run_view(identity.tenant_id, run_id))
            if method == "POST" and suffix == "/input":
                request = ApiInputRequest.model_validate(_body_json(body))
                request_id = (
                    request.request_id
                    or "api-request-"
                    + contract_sha256(
                        {
                            "tenant": identity.tenant_id,
                            "run": run_id,
                            "revision": run.revision,
                            "evidence": request.evidence,
                        }
                    )[:40]
                )
                ingress = (
                    None
                    if self.knowledge_ingress is None
                    else build_api_ingress(
                        ApiIngressRequest(
                            request_id=request_id,
                            run_id=run_id,
                            action="input",
                            text=json.dumps(
                                request.evidence,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            identity=identity,
                            revision=1,
                            occurred_at=occurred_at,
                            corrects_revision_ref=request.corrects_revision_ref,
                        )
                    )
                )
                _ = self.service.submit_input(
                    identity.tenant_id,
                    run_id,
                    request.evidence,
                    now=occurred_at,
                    admission=self._knowledge_admission(ingress),
                )
                return ApiResponse(HTTPStatus.ACCEPTED, self._run_view(identity.tenant_id, run_id))
            if method == "POST" and suffix == "/approval":
                if not self._can_approve(identity):
                    return ApiResponse(403, {"error": "agent_approval_permission_required"})
                request = ApiApprovalRequest.model_validate(_body_json(body))
                _ = self.service.decide_approval(
                    identity.tenant_id,
                    run_id,
                    approver_id=identity.principal_id,
                    granted=request.decision == "granted",
                    expires_at=request.expires_at,
                    expected_invocation_sha256=request.invocation_sha256,
                    now=occurred_at,
                )
                return ApiResponse(HTTPStatus.ACCEPTED, self._run_view(identity.tenant_id, run_id))
        except KnowledgePolicyError:
            return ApiResponse(HTTPStatus.FORBIDDEN, {"error": "knowledge_access_denied"})
        except (ValidationError, ValueError) as error:
            return ApiResponse(HTTPStatus.CONFLICT, {"error": _safe_error(error)})
        except CodexReasoningError:
            return ApiResponse(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "reasoning_provider_unavailable", "retryable": True},
            )
        return ApiResponse(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})

    def _can_approve(self, identity: OAuthIdentity) -> bool:
        """Authentication is not reviewer membership; only server policy can grant it."""
        if self.approval_authorizer is not None:
            try:
                return self.approval_authorizer(identity) is True
            except Exception:  # noqa: BLE001 - role lookup failure cannot grant execution authority.
                return False
        # A deliberately configured loopback token is the local operator surface.
        # OAuth/browser subjects need an explicit trusted reviewer mapping instead.
        return (
            self.oauth_authenticator is None
            and self.browser_login is None
            and bool(self.bearer_token)
            and identity == OAuthIdentity(self.tenant_id, self.principal_id)
        )

    def _identity(self, authorization: str | None) -> OAuthIdentity | None:
        if self.oauth_authenticator is not None:
            return self.oauth_authenticator.authenticate(authorization)
        if authorization is None:
            return None
        if not hmac.compare_digest(authorization, f"Bearer {self.bearer_token}"):
            return None
        return OAuthIdentity(tenant_id=self.tenant_id, principal_id=self.principal_id)

    def _run_view(self, tenant_id: str, run_id: str) -> JsonObject:
        run = self.service.repository.get(tenant_id, run_id)
        if run is None:
            raise ValueError("agent_run_not_found")
        return _JSON_OBJECT.validate_python(
            {
                "schema_version": "trace.agent-run-view.v1",
                "pending_invocation_sha256": next(
                    (
                        contract_sha256(ToolInvocation.model_validate(item.payload))
                        for item in reversed(self.service.repository.records(tenant_id, run_id))
                        if item.kind is AgentRecordKind.INVOCATION
                    ),
                    None,
                )
                if run.state.value == "awaiting_approval"
                else None,
                "run": run.model_dump(mode="json"),
                "steps": [
                    item.model_dump(mode="json")
                    for item in self.service.repository.steps(tenant_id, run_id)
                ],
                "records": [
                    item.model_dump(mode="json")
                    for item in self.service.repository.records(tenant_id, run_id)
                ],
            }
        )


def serve_marketing_agent_api(
    api: MarketingAgentApi,
    *,
    host: str,
    port: int,
    on_started: Callable[[], None] | None = None,
) -> None:
    if host not in {"127.0.0.1", "::1"} and api.oauth_authenticator is None:
        raise ValueError("agent_service_host_must_be_loopback")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        @override
        def log_message(self, format: str, *args: object) -> None:
            _ = format, args

        def _dispatch(self, method: str) -> None:
            maximum = _MAX_BODY_BYTES
            length = _content_length(self.headers.get("content-length"), maximum=maximum)
            if length > maximum:
                self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            body = self.rfile.read(length) if length else b""
            response = api.dispatch(
                method,
                self.path,
                authorization=self.headers.get("authorization"),
                body=body,
                headers={key.lower(): value for key, value in self.headers.items()},
            )
            payload = (
                response.body.encode()
                if isinstance(response.body, str)
                else json.dumps(
                    response.body,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            )
            self.send_response(response.status)
            for name, value in response.headers:
                self.send_header(name, value)
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("referrer-policy", "no-referrer")
            self.send_header("content-type", response.content_type)
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            _ = self.wfile.write(payload)

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        if on_started is not None:
            on_started()
        server.serve_forever()
    finally:
        server.server_close()


def _body_json(body: bytes) -> JsonObject:
    if not body or len(body) > _MAX_BODY_BYTES:
        raise ValueError("request_body_invalid")
    return _JSON_OBJECT.validate_json(body)


def _run_target(path: str) -> tuple[str, str]:
    prefix = "/v1/runs/"
    if not path.startswith(prefix):
        return "", path
    remainder = path[len(prefix) :]
    run_id, separator, tail = remainder.partition("/")
    if not run_id or "/" in tail:
        return "", path
    return run_id, f"/{tail}" if separator else ""


def _run_ui_path(path: str) -> str | None:
    prefix = "/runs/"
    if not path.startswith(prefix):
        return None
    run_id = path[len(prefix) :]
    if not run_id or "/" in run_id:
        return None
    return run_id


def _skill_run_target(path: str) -> str | None:
    prefix = "/v1/skills/"
    suffix = "/runs"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    skill_id = path[len(prefix) : -len(suffix)]
    if not skill_id or "/" in skill_id:
        return None
    return skill_id


def _context_transfer_validation_target(path: str) -> str | None:
    prefix = "/v1/knowledge/context-transfers/"
    suffix = "/validate"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    transfer_id = path[len(prefix) : -len(suffix)]
    if not transfer_id or "/" in transfer_id:
        return None
    return transfer_id


def _content_length(value: str | None, *, maximum: int = _MAX_BODY_BYTES) -> int:
    if value is None:
        return 0
    try:
        length = int(value)
    except ValueError:
        return maximum + 1
    return length if length >= 0 else maximum + 1


def _safe_error(error: ValidationError | ValueError) -> str:
    if isinstance(error, ValidationError):
        return "request_contract_invalid"
    return str(error) if str(error).startswith("agent_") else "request_rejected"


__all__ = ["ApiResponse", "MarketingAgentApi", "serve_marketing_agent_api"]

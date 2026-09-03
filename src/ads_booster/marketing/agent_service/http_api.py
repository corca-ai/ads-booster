"""Small authenticated HTTP boundary for the on-premises Marketing Agent Service."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Literal, override
from urllib.parse import urlsplit

from pydantic import Field, TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import AgentBudget, AgentGoal
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.agent_service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.marketing.agent_service.oauth import AccessTokenAuthenticator, OAuthIdentity
from ads_booster.marketing.agent_service.skills import MarketingSkillCatalog
from ads_booster.marketing.agent_service.web_ui import AGENT_RUN_UI
from ads_booster.providers.codex_reasoning import CodexReasoningError
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable

_MAX_BODY_BYTES = 1024 * 1024
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ApiCreateRunRequest(ContractModel):
    run_id: str
    goal: AgentGoal
    budget: AgentBudget


class ApiInputRequest(ContractModel):
    evidence: JsonObject


class ApiApprovalRequest(ContractModel):
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


@dataclass(frozen=True, slots=True)
class MarketingAgentApi:
    service: MarketingAgentService
    tenant_id: str
    principal_id: str
    bearer_token: str
    oauth_authenticator: AccessTokenAuthenticator | None = None

    def dispatch(  # noqa: C901,PLR0911,PLR0912 - explicit routes keep auth and scope visible.
        self,
        method: str,
        target: str,
        *,
        authorization: str | None,
        body: bytes = b"",
        now: datetime | None = None,
    ) -> ApiResponse:
        path = urlsplit(target).path
        if method == "GET" and (path == "/" or _run_ui_path(path) is not None):
            return ApiResponse(HTTPStatus.OK, AGENT_RUN_UI, "text/html; charset=utf-8")
        if method == "GET" and path == "/health":
            return ApiResponse(HTTPStatus.OK, {"status": "ok", "owner": "on_prem_agent"})
        identity = self._identity(authorization)
        if identity is None:
            return ApiResponse(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        occurred_at = datetime.now(UTC) if now is None else now
        try:
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
                run = self.service.create(
                    CreateAgentRunRequest(
                        run_id=request.run_id,
                        tenant_id=identity.tenant_id,
                        goal=request.goal,
                        budget=request.budget,
                    ),
                    now=occurred_at,
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
            if method == "GET" and suffix == "":
                return ApiResponse(HTTPStatus.OK, self._run_view(identity.tenant_id, run_id))
            if method == "POST" and suffix == "/input":
                request = ApiInputRequest.model_validate(_body_json(body))
                _ = self.service.submit_input(
                    identity.tenant_id, run_id, request.evidence, now=occurred_at
                )
                return ApiResponse(HTTPStatus.ACCEPTED, self._run_view(identity.tenant_id, run_id))
            if method == "POST" and suffix == "/approval":
                request = ApiApprovalRequest.model_validate(_body_json(body))
                _ = self.service.decide_approval(
                    identity.tenant_id,
                    run_id,
                    approver_id=identity.principal_id,
                    granted=request.decision == "granted",
                    expires_at=request.expires_at,
                    now=occurred_at,
                )
                return ApiResponse(HTTPStatus.ACCEPTED, self._run_view(identity.tenant_id, run_id))
        except (ValidationError, ValueError) as error:
            return ApiResponse(HTTPStatus.CONFLICT, {"error": _safe_error(error)})
        except CodexReasoningError:
            return ApiResponse(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "reasoning_provider_unavailable", "retryable": True},
            )
        return ApiResponse(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})

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
            length = _content_length(self.headers.get("content-length"))
            if length > _MAX_BODY_BYTES:
                self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            body = self.rfile.read(length) if length else b""
            response = api.dispatch(
                method,
                self.path,
                authorization=self.headers.get("authorization"),
                body=body,
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


def _content_length(value: str | None) -> int:
    if value is None:
        return 0
    try:
        length = int(value)
    except ValueError:
        return _MAX_BODY_BYTES + 1
    return max(0, length)


def _safe_error(error: ValidationError | ValueError) -> str:
    if isinstance(error, ValidationError):
        return "request_contract_invalid"
    return str(error) if str(error).startswith("agent_") else "request_rejected"


__all__ = ["ApiResponse", "MarketingAgentApi", "serve_marketing_agent_api"]

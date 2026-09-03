"""Small authenticated HTTP boundary for the on-premises Marketing Agent Service."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Literal, override
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import AgentBudget, AgentGoal
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.agent_service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.marketing.agent_service.web_ui import AGENT_RUN_UI
from ads_booster.providers.codex_reasoning import CodexReasoningError
from ads_booster.transport.json_types import JsonObject

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

    def dispatch(  # noqa: C901,PLR0911 - explicit route table keeps auth and tenant scope visible.
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
        if not self._authorized(authorization):
            return ApiResponse(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        occurred_at = datetime.now(UTC) if now is None else now
        try:
            if method == "POST" and path == "/v1/runs":
                request = ApiCreateRunRequest.model_validate(_body_json(body))
                run = self.service.create(
                    CreateAgentRunRequest(
                        run_id=request.run_id,
                        tenant_id=self.tenant_id,
                        goal=request.goal,
                        budget=request.budget,
                    ),
                    now=occurred_at,
                )
                return ApiResponse(HTTPStatus.ACCEPTED, self._run_view(run.run_id))
            if method == "GET" and path == "/v1/runs":
                return ApiResponse(
                    HTTPStatus.OK,
                    {
                        "runs": [
                            item.model_dump(mode="json")
                            for item in self.service.repository.list_runs(self.tenant_id)
                        ]
                    },
                )
            run_id, suffix = _run_target(path)
            run = self.service.repository.get(self.tenant_id, run_id)
            if run is None:
                return ApiResponse(HTTPStatus.NOT_FOUND, {"error": "agent_run_not_found"})
            if method == "GET" and suffix == "":
                return ApiResponse(HTTPStatus.OK, self._run_view(run_id))
            if method == "POST" and suffix == "/input":
                request = ApiInputRequest.model_validate(_body_json(body))
                _ = self.service.submit_input(
                    self.tenant_id, run_id, request.evidence, now=occurred_at
                )
                return ApiResponse(HTTPStatus.ACCEPTED, self._run_view(run_id))
            if method == "POST" and suffix == "/approval":
                request = ApiApprovalRequest.model_validate(_body_json(body))
                _ = self.service.decide_approval(
                    self.tenant_id,
                    run_id,
                    approver_id=self.principal_id,
                    granted=request.decision == "granted",
                    expires_at=request.expires_at,
                    now=occurred_at,
                )
                return ApiResponse(HTTPStatus.ACCEPTED, self._run_view(run_id))
        except (ValidationError, ValueError) as error:
            return ApiResponse(HTTPStatus.CONFLICT, {"error": _safe_error(error)})
        except CodexReasoningError:
            return ApiResponse(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "reasoning_provider_unavailable", "retryable": True},
            )
        return ApiResponse(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})

    def _authorized(self, authorization: str | None) -> bool:
        if authorization is None:
            return False
        return hmac.compare_digest(authorization, f"Bearer {self.bearer_token}")

    def _run_view(self, run_id: str) -> JsonObject:
        run = self.service.repository.get(self.tenant_id, run_id)
        if run is None:
            raise ValueError("agent_run_not_found")
        return _JSON_OBJECT.validate_python(
            {
                "schema_version": "trace.agent-run-view.v1",
                "run": run.model_dump(mode="json"),
                "steps": [
                    item.model_dump(mode="json")
                    for item in self.service.repository.steps(self.tenant_id, run_id)
                ],
                "records": [
                    item.model_dump(mode="json")
                    for item in self.service.repository.records(self.tenant_id, run_id)
                ],
            }
        )


def serve_marketing_agent_api(api: MarketingAgentApi, *, host: str, port: int) -> None:
    if host not in {"127.0.0.1", "::1"}:
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

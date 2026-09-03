from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import MarketingAgentService
from ads_booster.marketing.agent_service.http_api import MarketingAgentApi
from ads_booster.marketing.agent_service.oauth import OAuthIdentity
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.runtime import SqliteSessionStore
from ads_booster.providers.codex_reasoning import CodexReasoningError

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.marketing.agent_core.ports import ReasoningProvider

NOW = datetime(2026, 9, 3, tzinfo=UTC)


class StopReasoning:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        decision = ReasoningDecision(
            schema_version="trace.reasoning-decision.v1",
            action="stop",
            expected_outcome="A bounded strategy decision is recorded",
            reasoning_summary="No execution tool is needed",
        )
        return ReasoningResult(
            schema_version="trace.reasoning-result.v1",
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fake.reasoning",
                model_id="fake",
                request_sha256=contract_sha256(request),
                output_schema_sha256="a" * 64,
                decision_sha256=contract_sha256(decision),
            ),
        )


class FailedReasoning:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        _ = request
        message = "reasoning_provider_result_invalid"
        raise CodexReasoningError(message)


class WorkspaceAuthenticator:
    def authenticate(self, authorization: str | None) -> OAuthIdentity | None:
        if authorization != "Bearer oauth-token":
            return None
        return OAuthIdentity(tenant_id="oauth-workspace", principal_id="oauth-member")


def test_common_api_creates_and_reads_one_canonical_run(tmp_path: Path) -> None:
    api = _api(tmp_path)
    body = json.dumps(
        {
            "run_id": "run-one",
            "goal": {
                "objective": "Market the changing AI lock screen",
                "success_criteria": ["one experiment"],
                "context": {},
            },
            "budget": {"max_tool_calls": 2, "max_cost_units": 4},
        }
    ).encode()

    created = api.dispatch(
        "POST",
        "/v1/runs",
        authorization="Bearer secret",
        body=body,
        now=NOW,
    )
    fetched = api.dispatch("GET", "/v1/runs/run-one", authorization="Bearer secret")

    assert created.status == 202
    assert fetched.status == 200
    assert isinstance(created.body, dict)
    assert isinstance(fetched.body, dict)
    assert fetched.body["run"] == created.body["run"]
    steps = fetched.body["steps"]
    assert isinstance(steps, list)
    assert len(steps) == 2


def test_common_api_derives_tenant_and_rejects_missing_identity(tmp_path: Path) -> None:
    api = _api(tmp_path)

    unauthorized = api.dispatch("GET", "/v1/runs", authorization=None)
    health = api.dispatch("GET", "/health", authorization=None)

    assert unauthorized.status == 401
    assert health.body == {"status": "ok", "owner": "on_prem_agent"}


def test_oauth_identity_scopes_repository_reads_to_introspected_workspace(tmp_path: Path) -> None:
    api = _api(tmp_path)
    oauth_api = MarketingAgentApi(
        api.service,
        tenant_id="unused",
        principal_id="unused",
        bearer_token="",
        oauth_authenticator=WorkspaceAuthenticator(),
    )

    response = oauth_api.dispatch("GET", "/v1/runs", authorization="Bearer oauth-token")

    assert response.status == 200
    assert response.body == {"runs": []}
    assert oauth_api.dispatch("GET", "/v1/runs", authorization="Bearer wrong").status == 401


def test_reasoning_failure_returns_retryable_service_status_and_preserves_run(
    tmp_path: Path,
) -> None:
    api = _api(tmp_path, reasoning=FailedReasoning())
    body = json.dumps(
        {
            "run_id": "retryable-run",
            "goal": {
                "objective": "Choose a format",
                "success_criteria": ["one experiment"],
                "context": {},
            },
            "budget": {"max_tool_calls": 2, "max_cost_units": 4},
        }
    ).encode()

    response = api.dispatch("POST", "/v1/runs", authorization="Bearer secret", body=body, now=NOW)

    assert response.status == 503
    assert response.body == {
        "error": "reasoning_provider_unavailable",
        "retryable": True,
    }
    run = api.service.repository.get("trace", "retryable-run")
    assert run is not None
    assert run.state.value == "running"


def test_run_centric_ui_is_served_without_embedding_credentials(tmp_path: Path) -> None:
    response = _api(tmp_path).dispatch("GET", "/", authorization=None)

    assert response.status == 200
    assert response.content_type == "text/html; charset=utf-8"
    assert isinstance(response.body, str)
    assert "Run journey" in response.body
    assert "Bearer secret" not in response.body


def test_channel_result_url_serves_the_run_centric_ui(tmp_path: Path) -> None:
    response = _api(tmp_path).dispatch("GET", "/runs/run-one", authorization=None)

    assert response.status == 200
    assert response.content_type == "text/html; charset=utf-8"
    assert isinstance(response.body, str)
    assert "location.pathname.match" in response.body


def test_invalid_nested_run_ui_path_is_not_served(tmp_path: Path) -> None:
    response = _api(tmp_path).dispatch("GET", "/runs/run-one/extra", authorization=None)

    assert response.status == 401


def _api(root: Path, *, reasoning: ReasoningProvider | None = None) -> MarketingAgentApi:
    service = MarketingAgentService(
        repository=SqliteAgentRunRepository(root / "agent.sqlite3"),
        registry=ToolRegistry(()),
        reasoning=StopReasoning() if reasoning is None else reasoning,
        tools={},
        runtime_store=SqliteSessionStore(root / "agent.sqlite3"),
    )
    return MarketingAgentApi(
        service,
        tenant_id="trace",
        principal_id="member-one",
        bearer_token="secret",  # noqa: S106 - fake local API credential.
    )

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.runtime import SqliteSessionStore
from ads_booster.agent.service.scheduler import AgentSkillScheduler, DailySkillSchedule
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.channels.http.http_api import ApiResponse, MarketingAgentApi
from ads_booster.channels.http.oauth import OAuthIdentity
from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.providers.codex_reasoning import CodexReasoningError
from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault
from ads_booster.threads.privacy import ThreadsPrivacyCallbacks
from ads_booster.tools.descriptors import (
    notion_daily_descriptor,
    research_descriptor,
    slack_delivery_descriptor,
)
from tests.marketing.agent_service.completion_fixtures import (
    FixtureMarketingAgentService as MarketingAgentService,
)
from tests.marketing.agent_service.threads_callback_fixtures import (
    FAKE_APP_SECRET,
    signed_request,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.agent.core.ports import ReasoningProvider

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


def create_run(
    api: MarketingAgentApi,
    run_id: str,
    objective: str,
    success_criteria: list[str],
) -> ApiResponse:
    body = json.dumps(
        {
            "run_id": run_id,
            "goal": {
                "objective": objective,
                "success_criteria": success_criteria,
                "context": {},
            },
            "budget": {"max_tool_calls": 2, "max_cost_units": 4},
        }
    ).encode()
    return api.dispatch(
        "POST",
        "/v1/runs",
        authorization="Bearer secret",
        body=body,
        now=NOW,
    )


def test_common_api_creates_and_reads_one_canonical_run(tmp_path: Path) -> None:
    api = _api(tmp_path)
    created = create_run(
        api,
        "run-one",
        "Market the changing AI lock screen",
        ["one experiment"],
    )
    fetched = api.dispatch("GET", "/v1/runs/run-one", authorization="Bearer secret")

    assert created.status == 202
    assert fetched.status == 200
    assert isinstance(created.body, dict)
    assert isinstance(fetched.body, dict)
    assert fetched.body["run"] == created.body["run"]
    steps = fetched.body["steps"]
    assert isinstance(steps, list)
    run = fetched.body["run"]
    assert isinstance(run, dict)
    assert run["state"] == "completed"
    revision = run["revision"]
    assert isinstance(revision, int)
    assert len(steps) == revision - 1


def test_run_detail_projects_bounded_execution_status(tmp_path: Path) -> None:
    api = _api(tmp_path)
    _ = create_run(api, "status-run", "Return one answer", ["answer is assessed"])

    response = api.dispatch("GET", "/v1/runs/status-run", authorization="Bearer secret")

    assert response.status == 200
    assert isinstance(response.body, dict)
    execution = response.body["execution"]
    assert execution == {
        "schema_version": "trace.run-execution-summary.v1",
        "phase": "terminal",
        "next_action": "done",
        "last_progress_at": "2026-09-03T00:00:00Z",
        "last_progress_reason": "obligation_satisfied",
        "budgets": {
            "decisions": {"used": 2, "maximum": 64, "remaining": 62},
            "assessments": {"used": 1, "maximum": 3, "remaining": 2},
            "tools": {
                "calls": {"used": 0, "maximum": 2, "remaining": 2},
                "cost_units": {"used": 0, "maximum": 4, "remaining": 4},
            },
        },
        "queue": {"state": None, "claim_owner": None, "lease_expires_at": None},
        "wait_reason": None,
    }


def test_run_detail_projects_optional_queue_claim_without_queue_payloads(tmp_path: Path) -> None:
    api = _api(tmp_path, reasoning=FailedReasoning())
    _ = create_run(api, "leased-run", "Keep working", ["one result"])
    with closing(sqlite3.connect(api.service.repository.database_path)) as db, db:
        _ = db.execute(
            """CREATE TABLE agent_drive_work (
                tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, revision INTEGER NOT NULL,
                due_at TEXT NOT NULL, state TEXT NOT NULL, claim_owner TEXT,
                lease_expires_at TEXT, PRIMARY KEY(tenant_id,run_id))"""
        )
        _ = db.execute(
            "INSERT INTO agent_drive_work VALUES(?,?,?,?,?,?,?)",
            (
                "trace",
                "leased-run",
                2,
                NOW.isoformat(),
                "running",
                "worker-1",
                "2026-09-03T00:01:00+00:00",
            ),
        )

    response = api.dispatch("GET", "/v1/runs/leased-run", authorization="Bearer secret")

    assert response.status == 200
    assert isinstance(response.body, dict)
    execution = response.body["execution"]
    assert isinstance(execution, dict)
    assert execution["phase"] == "driving"
    assert execution["queue"] == {
        "state": "running",
        "claim_owner": "worker-1",
        "lease_expires_at": "2026-09-03T00:01:00Z",
    }


def test_common_api_derives_tenant_and_rejects_missing_identity(tmp_path: Path) -> None:
    api = _api(tmp_path)

    unauthorized = api.dispatch("GET", "/v1/runs", authorization=None)
    health = api.dispatch("GET", "/health", authorization=None)

    assert unauthorized.status == 401
    assert health.body == {"status": "ok", "owner": "on_prem_agent"}


def test_threads_data_deletion_callback_returns_public_completion_url(tmp_path: Path) -> None:
    # Given an unauthenticated Meta callback signed by the configured app.
    api = _privacy_api(tmp_path)
    body = urlencode({"signed_request": signed_request("provider-user-1")}).encode()

    # When Meta requests deletion and follows the returned status URL.
    accepted = api.dispatch(
        "POST", "/integrations/threads/data-deletion", authorization=None, body=body, now=NOW
    )
    assert isinstance(accepted.body, dict)
    status_url = accepted.body["url"]
    assert isinstance(status_url, str)
    status = api.dispatch("GET", status_url, authorization=None)

    # Then both public responses expose only the opaque completion receipt.
    assert accepted.status == 200
    assert status.status == 200
    assert isinstance(status.body, dict)
    assert status.body["status"] == "completed"
    assert status.body["confirmation_code"] == accepted.body["confirmation_code"]


def test_threads_provider_callback_rejects_tampered_signature(tmp_path: Path) -> None:
    # Given a provider callback whose signed request was modified in transit.
    api = _privacy_api(tmp_path)
    body = urlencode(
        {"signed_request": signed_request("provider-user-1") + "tampered"}
    ).encode()

    # When the unauthenticated callback reaches the HTTP boundary.
    response = api.dispatch(
        "POST", "/integrations/threads/deauthorize", authorization=None, body=body, now=NOW
    )

    # Then no signature oracle or authenticated route fallback is exposed.
    assert response.status == 403
    assert response.body == {"error": "threads_callback_rejected"}


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


def test_skill_catalog_reports_real_runtime_blockers(tmp_path: Path) -> None:
    response = _api(tmp_path).dispatch("GET", "/v1/skills", authorization="Bearer secret", now=NOW)

    assert response.status == 200
    assert isinstance(response.body, dict)
    skills = response.body["skills"]
    assert isinstance(skills, list)
    first = skills[0]
    assert isinstance(first, dict)
    assert first["skill_id"] == "research.daily_slack"
    assert first["ready"] is False
    assert first["blockers"] == ["research.web", "deliver.slack"]


def test_ready_skill_creates_canonical_run_with_versioned_procedure(tmp_path: Path) -> None:
    api = _api(tmp_path)
    api.service.registry = ToolRegistry(
        (
            research_descriptor(installation_id="research", observed_at=NOW, ready=True),
            slack_delivery_descriptor(installation_id="slack", observed_at=NOW, ready=True),
            notion_daily_descriptor(installation_id="notion", observed_at=NOW, ready=True),
        )
    )
    body = json.dumps(
        {
            "run_id": "daily-2026-09-03",
            "context": {"research_request": {"session_id": "research-1"}},
        }
    ).encode()

    tools_response = api.dispatch("GET", "/v1/tools", authorization="Bearer secret", now=NOW)

    response = api.dispatch(
        "POST",
        "/v1/skills/research.daily_slack/runs",
        authorization="Bearer secret",
        body=body,
        now=NOW,
    )

    assert response.status == 202
    assert isinstance(tools_response.body, dict)
    tools = tools_response.body["tools"]
    assert isinstance(tools, list)
    assert all(isinstance(item, dict) for item in tools)
    assert [item["capability_id"] for item in tools if isinstance(item, dict)] == [
        "deliver.slack",
        "research.web",
        "store.notion.daily",
    ]
    run = api.service.repository.get("trace", "daily-2026-09-03")
    assert run is not None
    assert run.goal.context["skill_id"] == "research.daily_slack"
    assert "deliver.slack" in run.goal.objective


def test_daily_scheduler_uses_date_stable_canonical_skill_run(tmp_path: Path) -> None:
    api = _api(tmp_path)
    api.service.registry = ToolRegistry(
        (
            research_descriptor(installation_id="research", observed_at=NOW, ready=True),
            slack_delivery_descriptor(installation_id="slack", observed_at=NOW, ready=True),
            notion_daily_descriptor(installation_id="notion", observed_at=NOW, ready=True),
        )
    )
    scheduler = AgentSkillScheduler(
        api.service,
        (
            DailySkillSchedule(
                skill_id="research.daily_slack",
                tenant_id="trace",
                principal_id="scheduled-service",
                timezone="Asia/Seoul",
                hour=8,
                minute=0,
                context={"research_request": {"session_id": "research-1"}},
            ),
        ),
    )

    first = scheduler.tick(now=NOW)
    second = scheduler.tick(now=NOW)

    assert first == second == ("scheduled-research-daily-slack-2026-09-03",)
    assert len(api.service.repository.list_runs("trace")) == 1


def test_reasoning_failure_returns_retryable_service_status_and_preserves_run(
    tmp_path: Path,
) -> None:
    api = _api(tmp_path, reasoning=FailedReasoning())
    response = create_run(api, "retryable-run", "Choose a format", ["one experiment"])

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


def _privacy_api(root: Path) -> MarketingAgentApi:
    base = _api(root)
    database_path = base.service.repository.database_path
    callbacks = ThreadsPrivacyCallbacks(
        database_path,
        "https://agent.example.com",
        FAKE_APP_SECRET,
        ThreadsAccountRepository(database_path),
        ThreadsTokenVault(root / "threads-secrets"),
    )
    return MarketingAgentApi(
        base.service,
        tenant_id="trace",
        principal_id="member-one",
        bearer_token="secret",  # noqa: S106 - fake local API credential.
        threads_privacy=callbacks,
    )

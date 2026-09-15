from __future__ import annotations

import sqlite3
from contextlib import closing, nullcontext
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import pytest

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.runtime import SqliteSessionStore
from ads_booster.agent.service.application import CreateAgentRunRequest, MarketingAgentService
from ads_booster.agent.service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.agent.service.task_completion import TaskCompletionService
from ads_booster.agent.service.task_progress import project_task
from ads_booster.channels.task_results import result_for
from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, AgentRecordKind, AgentRunState
from ads_booster.contracts.reasoning import ReasoningDecision
from ads_booster.contracts.threads import ThreadsActor
from ads_booster.providers.threads_api import ThreadsApiClient
from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault
from ads_booster.threads.effect_fence import ThreadsEffectFence
from ads_booster.threads.oauth import ThreadsOAuthService
from ads_booster.tools.compatibility import DelegatedToolResult, DelegatingToolAdapter
from ads_booster.tools.completion_proofs import CanonicalCompletionProofs, CompletionArtifactOwners
from ads_booster.tools.threads_connection import ThreadsConnectTool
from ads_booster.tools.threads_connection import descriptor as connect_descriptor
from tests.marketing.agent_service.completion_fixtures import NOW
from tests.marketing.agent_service.test_task_completion import CompletionScript
from tests.marketing.agent_service.threads_callback_fixtures import FAKE_APP_SECRET

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ads_booster.contracts.agent_run import AgentRun, ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor


class SimulatedRestartError(RuntimeError):
    pass


def actor(tenant_id: str, run_id: str) -> ThreadsActor:
    return ThreadsActor(
        workspace_id=tenant_id, member_id="member", conversation_id=run_id, source_event_id="event"
    )


def drive_connect(
    service: Callable[[], MarketingAgentService], restart: str
) -> tuple[MarketingAgentService, AgentRun, datetime]:
    current = service()
    run = current.create(
        CreateAgentRunRequest(
            run_id="connect",
            tenant_id="trace",
            goal=AgentGoal(
                objective="Connect my Threads account", success_criteria=("Account connected",)
            ),
            budget=AgentBudget(max_tool_calls=4, max_cost_units=10),
        ),
        now=NOW,
    )

    def interrupt(point: str) -> None:
        if point == "verify_committed":
            raise SimulatedRestartError

    run = current.drive("trace", run.run_id, now=NOW)
    interrupted = restart in {"verified", "expired", "consumed"}
    if interrupted:
        current.fault_hook = interrupt
    with pytest.raises(SimulatedRestartError) if interrupted else nullcontext():
        run = current.decide_approval(
            "trace",
            run.run_id,
            approver_id="member",
            granted=True,
            now=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
    if interrupted:
        current = service()
    resume_time = datetime.now(UTC) + timedelta(minutes=11) if restart == "expired" else NOW
    if restart == "consumed":
        with closing(sqlite3.connect(current.repository.database_path)) as connection, connection:
            _ = connection.execute("UPDATE threads_oauth_states SET consumed=1")
    return current, current.drive("trace", run.run_id, now=resume_time), resume_time


@pytest.mark.parametrize(
    "restart",
    ["none", "verified", "expired", "consumed", "invalid_expiry", "failed", "unknown_side_effect"],
)
def test_connect_delivers_input_handoff_without_replanning(
    tmp_path: Path,
    restart: Literal[
        "none", "verified", "expired", "consumed", "invalid_expiry", "failed", "unknown_side_effect"
    ],
) -> None:
    # Given real OAuth state persistence and a planner that would repeat connect if called again.
    database = tmp_path / "agent.sqlite3"
    accounts = ThreadsAccountRepository(database)
    api = ThreadsApiClient.create(app_id="fixture", app_secret=FAKE_APP_SECRET)
    oauth = ThreadsOAuthService(
        str(database),
        "https://agent.example.com/callback",
        api,
        accounts,
        ThreadsTokenVault(tmp_path / "tokens"),
        ThreadsEffectFence(database),
    )
    tool = ThreadsConnectTool(oauth, actor)

    def execute(invocation: ToolInvocation, descriptor: ToolDescriptor) -> DelegatedToolResult:
        result = tool.execute(invocation, descriptor)
        if restart == "invalid_expiry":
            with closing(sqlite3.connect(database)) as connection, connection:
                _ = connection.execute("UPDATE threads_oauth_states SET expires_at='invalid'")
            return result.model_copy(update={"output": {**result.output, "expires_at": "invalid"}})
        if restart in {"failed", "unknown_side_effect"}:
            return result.model_copy(update={"disposition": restart})
        return result

    planner = CompletionScript(
        (
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id="threads.connect",
                tool_input={},
                expected_outcome="Connect Threads",
                reasoning_summary="Request consent",
            ),
        )
    )

    def service() -> MarketingAgentService:
        repository = SqliteAgentRunRepository(database)
        return MarketingAgentService(
            repository=repository,
            registry=ToolRegistry((connect_descriptor(now=NOW),)),
            reasoning=planner,
            tools={
                "threads.connect": DelegatingToolAdapter(
                    capability_id="threads.connect",
                    version="1",
                    executor_id="threads-oauth",
                    executor=execute,
                )
            },
            runtime_store=SqliteSessionStore(database),
            completion=TaskCompletionService(
                repository,
                None,
                CanonicalCompletionProofs(
                    repository,
                    CompletionArtifactOwners(database_path=database),
                ),
            ),
            clock=lambda: NOW,
        )

    # When the successful connect result is evaluated, including after a durable VERIFY restart.
    try:
        current, run, resume_time = drive_connect(service, restart)
        records = current.repository.records("trace", run.run_id)
        output = next(
            record.payload["output"]
            for record in records
            if record.payload_schema_version == "trace.tool-output-evidence.v1"
        )
        assert isinstance(output, dict)
        if restart in {"failed", "unknown_side_effect", "invalid_expiry"}:
            assert run.state is not AgentRunState.AWAITING_INPUT
            assert str(output["authorization_url"]) not in result_for(run, records).text
            return
        # Then the existing channel result carries the exact link as waiting, never completion.
        assert run.state is AgentRunState.AWAITING_INPUT
        result = result_for(run, records)
        if restart in {"expired", "consumed"}:
            assert str(output["authorization_url"]) not in result.text
        else:
            assert str(output["authorization_url"]) in result.text
            assert str(output["expires_at"]) in result.text
        assert result.task_disposition == "waiting"
        assert result.identity is None
        assert project_task(run, records).checkpoint.candidate is None
        assert len(planner.requests) == 1
        assert accounts.list_for_workspace("trace") == ()
        assert sum(record.kind is AgentRecordKind.INVOCATION for record in records) == 1
        restarted = service()
        assert restarted.drive("trace", run.run_id, now=resume_time) == run
        assert result_for(run, restarted.repository.records("trace", run.run_id)) == result
        assert len(planner.requests) == 1
        if restart == "none":
            planner.decisions = (
                ReasoningDecision(
                    schema_version="trace.reasoning-decision.v1",
                    action="request_input",
                    expected_outcome="Check the account after the user's report",
                    reasoning_summary="Which account did you authorize?",
                ),
            )
            resumed = restarted.submit_input(
                "trace",
                run.run_id,
                {"text": "I completed consent"},
                now=NOW,
            )
            after = restarted.repository.records("trace", run.run_id)
            assert resumed.revision > run.revision
            assert len(planner.requests) == 2
            assert str(output["authorization_url"]) not in result_for(resumed, after).text
            assert sum(record.kind is AgentRecordKind.INVOCATION for record in after) == 1
    finally:
        api.close()

from __future__ import annotations

import json
from contextlib import closing
from typing import TYPE_CHECKING, override

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.agent.service.task_completion import TaskCompletionService
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState, contract_sha256
from ads_booster.contracts.reasoning import ReasoningDecision
from ads_booster.contracts.threads import ThreadsActor
from ads_booster.providers.threads_api import ThreadsApiClient
from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault
from ads_booster.threads.effect_fence import ThreadsEffectFence
from ads_booster.threads.oauth import ThreadsOAuthService
from ads_booster.tools.compatibility import DelegatingToolAdapter
from ads_booster.tools.completion_proofs import CanonicalCompletionProofs, CompletionArtifactOwners
from ads_booster.tools.threads_connection import CAPABILITY, ThreadsConnectTool, descriptor
from tests.marketing.agent_service.test_task_completion import CompletionScript
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult


class RequestedConnectReasoning(CompletionScript):
    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        result = super().plan(request)
        decision = result.decision.model_copy(
            update={"authorization_message": request.current_user_message}
        )
        return result.model_copy(
            update={
                "decision": decision,
                "receipt": result.receipt.model_copy(
                    update={"decision_sha256": contract_sha256(decision)}
                ),
            }
        )


def test_signed_connect_request_delivers_oauth_handoff_once_in_original_thread(
    tmp_path: Path,
) -> None:
    # Given signed Slack ingress, a real OAuth owner and canonical completion proofs.
    owner, messages = setup_events(tmp_path)
    service = owner.commands.application.service
    database = tmp_path / "agent.sqlite3"
    accounts = ThreadsAccountRepository(database)
    planner = RequestedConnectReasoning(
        (
            ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id=CAPABILITY,
                tool_input={},
                expected_outcome="Connect the requested Threads account",
                reasoning_summary="Request Threads consent",
            ),
        )
    )

    def actor(tenant_id: str, run_id: str) -> ThreadsActor:
        return ThreadsActor(
            workspace_id=tenant_id,
            member_id="member",
            conversation_id=run_id,
            source_event_id="Ev1",
        )

    with closing(ThreadsApiClient.create(app_id="fixture", app_secret="")) as api:
        oauth = ThreadsOAuthService(
            str(database),
            "https://agent.example.com/callback",
            api,
            accounts,
            ThreadsTokenVault(tmp_path / "tokens"),
            ThreadsEffectFence(database),
        )
        tool = ThreadsConnectTool(oauth, actor)
        service.registry = ToolRegistry((descriptor(now=NOW),))
        service.reasoning = planner
        service.tools = {
            CAPABILITY: DelegatingToolAdapter(
                capability_id=CAPABILITY,
                version="1",
                executor_id="threads-oauth",
                executor=tool.execute,
            )
        }
        service.completion = TaskCompletionService(
            service.repository,
            None,
            CanonicalCompletionProofs(
                service.repository, CompletionArtifactOwners(database_path=database)
            ),
        )
        text = "<@UBOT> 내 Threads 계정을 연결해줘"

        # When the explicit request is processed and the same event replays after restart.
        receive(owner, text=text)
        assert owner.work_once(now=NOW)
        run = service.repository.list_runs("team")[0]
        records = service.repository.records("team", run.run_id)
        output = next(
            record.payload["output"]
            for record in records
            if record.payload_schema_version == "trace.tool-output-evidence.v1"
        )
        assert isinstance(output, dict)

        # Then consent is required, the exact issued URL is delivered, and no work repeats.
        assert run.state is AgentRunState.AWAITING_INPUT
        assert len(planner.requests) == 1
        assert sum(record.kind is AgentRecordKind.INVOCATION for record in records) == 1
        approvals = [record for record in records if record.kind is AgentRecordKind.APPROVAL]
        assert len(approvals) == 1
        assert approvals[0].payload["request_event_id"]
        assert approvals[0].payload["request_text_sha256"]
        assert messages[0]["thread_ts"] == "100.001"
        assert all(message["channel"] == "C1" for message in messages)
        assert messages[-1]["ts"] == "123.456"
        assert str(output["authorization_url"]) in str(messages[-1]["text"])
        assert str(output["expires_at"]) in str(messages[-1]["text"])
        assert accounts.list_for_workspace("team") == ()
        delivered_count = len(messages)
        restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
        restarted.recover()
        receive(restarted, text=text)
        assert not restarted.work_once(now=NOW)
        assert len(messages) == delivered_count
        assert len(planner.requests) == 1
        assert service.repository.records("team", run.run_id) == records
        _ = (tmp_path / "slack-messages.json").write_text(json.dumps(messages, ensure_ascii=False))

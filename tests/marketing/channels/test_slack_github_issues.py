from __future__ import annotations

from typing import TYPE_CHECKING, override

import pytest

from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.bootstrap import integrations
from ads_booster.channels.slack_events import SlackEvents
from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState
from ads_booster.contracts.reasoning import ReasoningDecision
from ads_booster.tools.github_issues import CAPABILITY, GitHubIssues, token_from_env
from tests.marketing.agent_service.test_application import (
    _reasoning_result,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.agent_service.test_github_issues import PAYLOAD, URL, Response
from tests.marketing.agent_service.test_integrations import UnusedResearchRunner
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events

if TYPE_CHECKING:
    from pathlib import Path
    from urllib.request import Request

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.transport.json_types import JsonObject


class IssueReasoning:
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        available = any(
            d.capability_id == CAPABILITY for d in request.capability_snapshot.descriptors
        )
        if not available or request.evidence:
            decision = ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="stop",
                expected_outcome="Answer",
                reasoning_summary="처리 결과를 확인해주세요.",
            )
        else:
            decision = ReasoningDecision(
                schema_version="trace.reasoning-decision.v1",
                action="invoke_tool",
                capability_id=CAPABILITY,
                tool_input=PAYLOAD,
                expected_outcome="Create requested issue",
                reasoning_summary="이슈 내용을 검토해주세요.",
            )
        return _reasoning_result(request, decision)


class RequestedIssueReasoning(IssueReasoning):
    @override
    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        result = super().plan(request)
        if result.decision.action != "invoke_tool":
            return result
        return _reasoning_result(
            request,
            result.decision.model_copy(
                update={
                    "authorization_message": request.current_user_message,
                }
            ),
        )


def test_explicit_issue_request_executes_once_without_repeated_consent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, messages, calls = configured_events(tmp_path, monkeypatch)
    owner.commands.application.service.reasoning = RequestedIssueReasoning()
    text = "<@UBOT> 이 승인 반복 오류를 깃허브 이슈로 올려줘"
    receive(owner, text=text)
    assert owner.work_once(now=NOW)
    assert calls == ["POST", "GET"]
    assert URL in str(messages[-1]["text"])
    service = owner.commands.application.service
    run = service.repository.list_runs("team")[0]
    approvals = [
        r
        for r in service.repository.records("team", run.run_id)
        if r.kind is AgentRecordKind.APPROVAL
    ]
    assert len(approvals) == 1
    assert approvals[0].payload["request_event_id"]
    assert approvals[0].payload["request_text_sha256"]
    restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    restarted.recover()
    receive(restarted, text=text)
    assert not restarted.work_once(now=NOW)
    assert calls == ["POST", "GET"]


@pytest.mark.parametrize("cause", ["private", "revoked", "wrong_message"])
def test_model_interpretation_cannot_bypass_current_host_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cause: str,
) -> None:
    owner, _, calls = configured_events(tmp_path, monkeypatch)
    service = owner.commands.application.service

    class Proposal(RequestedIssueReasoning):
        @override
        def plan(self, request: ReasoningRequest) -> ReasoningResult:
            result = super().plan(request)
            if cause == "revoked":
                identity = owner.identity("U1")
                owner.commands.application.store.put_identity(
                    identity.model_copy(update={"can_approve": False})
                )
            if cause == "wrong_message" and result.decision.action == "invoke_tool":
                return _reasoning_result(
                    request,
                    result.decision.model_copy(
                        update={
                            "authorization_message": "이전 대화에서 이슈 올려줘",
                        }
                    ),
                )
            return result

    service.reasoning = Proposal()
    if cause == "private":
        receive(owner, type="message", channel="D1", channel_type="im", text="이슈 올려줘")
    else:
        receive(owner, text="<@UBOT> 이슈 올려줘")
    assert owner.work_once(now=NOW)
    assert not calls


def test_direct_issue_unknown_result_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, _, calls = configured_events(tmp_path, monkeypatch, lost=True)
    owner.commands.application.service.reasoning = RequestedIssueReasoning()
    receive(owner, text="<@UBOT> 이 오류를 이슈로 등록해줘")
    assert owner.work_once(now=NOW)
    assert (
        owner.commands.application.service.repository.list_runs("team")[0].state
        is AgentRunState.AWAITING_RECONCILIATION
    )
    restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    restarted.recover()
    assert not restarted.work_once(now=NOW)
    assert calls == ["POST"]


def configured_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, lost: bool = False, auth: str = "GH_TOKEN"
) -> tuple[SlackEvents, list[JsonObject], list[str]]:
    monkeypatch.setenv("HOME", str(tmp_path))
    env = {auth: "fixture_token"}
    if auth == "gh":
        binary = tmp_path / "gh"
        _ = binary.write_text("#!/bin/sh\nprintf 'fixture_token\\n'\n")
        binary.chmod(0o700)
        env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    calls: list[str] = []

    def opener(request: Request, *, timeout: float) -> Response:
        _ = timeout
        calls.append(request.get_method())
        if lost:
            raise TimeoutError
        return Response(
            {"number": 123, "html_url": URL, "title": PAYLOAD["title"], "body": PAYLOAD["body"]},
            201 if request.method == "POST" else 200,
        )

    def factory(token: str) -> GitHubIssues:
        return GitHubIssues(token, opener)

    monkeypatch.setattr(integrations, "GitHubIssues", factory)
    config = integrations.ConfiguredAgentTools(
        integrations.AgentServiceIntegrationConfig(
            github_token=token_from_env(env),
        ),
        UnusedResearchRunner(),
    )
    owner, messages = setup_events(tmp_path)
    service = owner.commands.application.service
    service.registry = ToolRegistry(config.descriptors(now=NOW))
    service.tools = config.adapters()
    service.reasoning = IssueReasoning()
    return SlackEvents(owner.commands, "UBOT", frozenset({"C1"})), messages, calls


def approve(owner: SlackEvents, text: str) -> None:
    receive(owner, type="message", text=text, ts="100.003", thread_ts="100.001")
    assert owner.work_once(now=NOW)


@pytest.mark.parametrize("auth", ["GH_TOKEN", "GITHUB_TOKEN", "gh"])
def test_signed_slack_issue_review_approval_readback_url_and_duplicate_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auth: str
) -> None:
    owner, messages, calls = configured_events(tmp_path, monkeypatch, auth=auth)
    receive(owner, text="<@UBOT> ads-booster 저장소에 이 오류 이슈 올려줘")
    assert owner.work_once(now=NOW)
    assert not calls
    assert (
        owner.commands.application.service.repository.list_runs("team")[0].state
        is AgentRunState.AWAITING_APPROVAL
    )
    approval = next(
        line for line in str(messages[-1]["text"]).splitlines() if line.startswith("승인 ")
    )
    receive(owner, type="message", text="검토 1", ts="100.002", thread_ts="100.001")
    assert owner.work_once(now=NOW)
    assert "corca-ai/ads-booster" in str(messages[-1]["text"])
    assert str(PAYLOAD["title"]) in str(messages[-1]["text"])
    assert not calls
    approve(owner, approval)
    assert calls == ["POST", "GET"]
    assert URL in str(messages[-1]["text"])
    run = owner.commands.application.service.repository.list_runs("team")[0]
    assert run.state is AgentRunState.COMPLETED
    assert URL in owner.commands.summary("team", run.run_id)
    restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    restarted.recover()
    receive(restarted, type="message", text=approval, ts="100.003", thread_ts="100.001")
    assert not restarted.work_once(now=NOW)
    assert calls == ["POST", "GET"]


def test_lost_create_response_blocks_run_without_retry_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, messages, calls = configured_events(tmp_path, monkeypatch, lost=True)
    receive(owner)
    assert owner.work_once(now=NOW)
    approval = next(
        line for line in str(messages[-1]["text"]).splitlines() if line.startswith("승인 ")
    )
    approve(owner, approval)
    assert (
        owner.commands.application.service.repository.list_runs("team")[0].state
        is AgentRunState.AWAITING_RECONCILIATION
    )
    assert URL not in str(messages[-1]["text"])
    restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
    restarted.recover()
    assert not restarted.work_once(now=NOW)
    assert calls == ["POST"]


def test_private_dm_does_not_gain_repository_write_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _, calls = configured_events(tmp_path, monkeypatch)
    receive(owner, type="message", channel="D1", channel_type="im", text="이슈 올려줘")
    assert owner.work_once(now=NOW)
    assert not calls

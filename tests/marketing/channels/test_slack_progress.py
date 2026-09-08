from __future__ import annotations

import json
import time
from dataclasses import replace
from threading import Event, Thread
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import pytest

from ads_booster.contracts.agent_run import AgentRunState
from ads_booster.execution_control import checkpoint
from ads_booster.bootstrap import integrations
from ads_booster.tools.github_issues import GitHubIssues
from ads_booster.channels.http.http_api import MarketingAgentApi
from ads_booster.agent.service.maintenance import MaintenanceGate
from ads_booster.channels import slack_events
from ads_booster.channels.slack import slack_signature
from ads_booster.channels.slack_events import SlackEvents
from tests.marketing.agent_service.test_github_issues import PAYLOAD, URL, Response
from tests.marketing.agent_service.test_http_api import StopReasoning
from tests.marketing.agent_service.test_integrations import UnusedResearchRunner
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events
from tests.marketing.channels.test_slack_github_issues import configured_events

if TYPE_CHECKING:
    from pathlib import Path
    from urllib.request import Request

    from ads_booster.contracts.reasoning import ReasoningRequest, ReasoningResult
    from ads_booster.transport.json_types import JsonObject


class WaitingReasoning:
    def __init__(self) -> None:
        self.started: Event = Event()
        self.release: Event = Event()

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        self.started.set()
        while not self.release.wait(0.02):
            checkpoint("답변 작성 중")
        checkpoint("답변 완료")
        return StopReasoning().plan(request)


def button_value(payload: JsonObject) -> str:
    blocks = payload["blocks"]
    assert isinstance(blocks, list)
    action = blocks[-1]
    assert isinstance(action, dict)
    elements = action["elements"]
    assert isinstance(elements, list)
    assert isinstance(elements[0], dict)
    return str(elements[0]["value"])


def interaction(
    message_id: str,
    *,
    user: str = "U1",
    channel: str = "C1",
    timestamp: str = "123.456",
    app: str = "A1",
) -> tuple[bytes, dict[str, str]]:
    body = urlencode(
        {
            "payload": json.dumps(
                {
                    "type": "block_actions",
                    "api_app_id": app,
                    "team": {"id": "T1"},
                    "user": {"id": user},
                    "container": {"channel_id": channel, "message_ts": timestamp},
                    "actions": [{"action_id": "trace_stop_run", "value": message_id}],
                }
            )
        }
    ).encode()
    stamp = str(int(NOW.timestamp()))
    return body, {
        "x-slack-request-timestamp": stamp,
        "x-slack-signature": slack_signature(b"test-secret", body, stamp),
    }


def test_working_message_is_updated_and_stop_is_acknowledged_while_reasoning_runs(
    tmp_path: Path,
) -> None:
    owner, messages = setup_events(tmp_path)
    reasoning = WaitingReasoning()
    service = owner.commands.application.service
    service.reasoning = reasoning
    receive(owner)
    worker = Thread(target=lambda: owner.work_once(now=NOW))
    worker.start()
    try:
        assert reasoning.started.wait(3)
        assert "접수했습니다" not in str(messages[0])
        assert "실행 중단" in str(messages[0])
        message_id = button_value(messages[0])
        api = MarketingAgentApi(
            service, "team", "member", "secret", slack_events=owner, slack_only=True
        )
        gate = MaintenanceGate(tmp_path / "maintenance", "fixture")
        _ = (tmp_path / "maintenance").write_text("draining")
        api = replace(api, maintenance=gate)
        body, headers = interaction(message_id)
        started = time.monotonic()
        assert (
            api.dispatch(
                "POST",
                "/channels/slack/interactions",
                authorization=None,
                body=body,
                headers=headers,
                now=NOW,
            ).status
            == 200
        )
        assert time.monotonic() - started < 1
        worker.join(3)
        assert not worker.is_alive()
        assert service.repository.list_runs("team")[0].state is AgentRunState.STOPPED
        assert "중단했습니다" in str(messages[-1]["text"])
        assert messages[-1]["ts"] == "123.456"
        assert "실행 중단" not in str(messages[-1]["blocks"])
        restarted = SlackEvents(owner.commands, "UBOT", frozenset({"C1"}))
        restarted.recover()
        assert not restarted.work_once(now=NOW)
        assert restarted.progress.cancelled(message_id)
        # A delayed duplicate button cannot stop the next request in this thread.
        service.reasoning = StopReasoning()
        receive(restarted, type="message", text="다음 질문", ts="100.004", thread_ts="100.001")
        assert restarted.interact(body, headers, now=NOW) == {}
        assert restarted.work_once(now=NOW)
        assert service.repository.list_runs("team")[-1].state is AgentRunState.COMPLETED
    finally:
        reasoning.release.set()
        worker.join(3)


@pytest.mark.parametrize(
    "change", [{"user": "U2"}, {"channel": "C2"}, {"timestamp": "999.111"}, {"app": "A2"}]
)
def test_stop_button_rejects_wrong_identity_or_message(
    tmp_path: Path, change: dict[str, str]
) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner)
    assert owner.work_once(now=NOW)
    if change.get("user") == "U2":
        owner.commands.allowed_user_ids = frozenset({"U1", "U2"})
        binding = owner.identity("U1").model_copy(
            update={
                "binding_id": "slack-T1-U2",
                "external_user_id": "U2",
                "member_id": "other",
                "can_approve": False,
            }
        )
        owner.commands.application.store.put_identity(binding)
    body, headers = interaction(button_value(messages[0]), **change)
    with pytest.raises(ValueError, match=r"slack_|channel identity"):
        _ = owner.interact(body, headers, now=NOW)


def test_forged_button_signature_is_rejected(tmp_path: Path) -> None:
    owner, messages = setup_events(tmp_path)
    receive(owner)
    assert owner.work_once(now=NOW)
    body, headers = interaction(button_value(messages[0]))
    with pytest.raises(ValueError, match="signature"):
        _ = owner.interact(body + b"x", headers, now=NOW)


def test_progress_refreshes_elapsed_time_then_final_update_removes_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    monkeypatch.setattr(slack_events, "_PROGRESS_INTERVAL_SECONDS", 0.02)
    owner, messages = setup_events(tmp_path)
    reasoning = WaitingReasoning()
    owner.commands.application.service.reasoning = reasoning
    receive(owner)
    worker = Thread(target=lambda: owner.work_once(now=NOW))
    worker.start()
    try:
        assert reasoning.started.wait(3)
        deadline = time.monotonic() + 3
        while len(messages) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "초 경과" in str(messages[-1]["text"])
        assert messages[-1]["ts"] == "123.456"
        assert "실행 중단" in str(messages[-1]["blocks"])
        reasoning.release.set()
        worker.join(3)
        assert not worker.is_alive()
        assert "실행 중단" not in str(messages[-1]["blocks"])
        count = len(messages)
        time.sleep(0.05)
        assert len(messages) == count
    finally:
        reasoning.release.set()
        worker.join(3)


@pytest.mark.parametrize("lost", [False, True])
def test_stop_during_issue_post_preserves_receipt_or_uncertainty_without_reposting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lost: bool
) -> None:

    owner, messages, _ = configured_events(tmp_path, monkeypatch)
    entered, release = Event(), Event()
    calls: list[str] = []

    def opener(request: Request, *, timeout: float) -> Response:
        _ = timeout
        calls.append(request.get_method())
        if request.get_method() == "POST":
            entered.set()
            assert release.wait(3)
            if lost:
                raise TimeoutError
        return Response(
            {"number": 123, "html_url": URL, "title": PAYLOAD["title"], "body": PAYLOAD["body"]},
            201 if request.get_method() == "POST" else 200,
        )

    def factory(token: str) -> GitHubIssues:
        return GitHubIssues(token, opener)

    monkeypatch.setattr(integrations, "GitHubIssues", factory)
    service = owner.commands.application.service
    service.tools = integrations.ConfiguredAgentTools(
        integrations.AgentServiceIntegrationConfig(
            github_token="fixture"  # noqa: S106 - fixture credential
        ),
        UnusedResearchRunner(),
    ).adapters()
    receive(owner)
    assert owner.work_once(now=NOW)
    approval = str(messages[-1]["text"]).split("\n")[1]
    receive(owner, type="message", text=approval, ts="100.002", thread_ts="100.001")
    worker = Thread(target=lambda: owner.work_once(now=NOW))
    worker.start()
    try:
        assert entered.wait(3)
        body, headers = interaction(button_value(messages[-1]))
        assert owner.interact(body, headers, now=NOW) == {}
        release.set()
        worker.join(3)
        assert not worker.is_alive()
        expected = AgentRunState.AWAITING_RECONCILIATION if lost else AgentRunState.STOPPED
        assert service.repository.list_runs("team")[0].state is expected
        assert calls == (["POST"] if lost else ["POST", "GET"])
        if lost:
            assert "확인이 필요" in str(messages[-1]["text"])
        else:
            assert URL in str(messages[-1]["text"])
        owner.recover()
        assert not owner.work_once(now=NOW)
    finally:
        release.set()
        worker.join(3)

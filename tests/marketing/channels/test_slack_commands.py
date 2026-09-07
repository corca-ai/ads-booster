from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import pytest

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.application import MarketingAgentService
from ads_booster.marketing.agent_service.channel_setup import slack_from_env
from ads_booster.marketing.agent_service.sqlite_repository import SqliteAgentRunRepository
from ads_booster.marketing.channels.slack import slack_signature
from ads_booster.marketing.runtime import SqliteSessionStore
from tests.marketing.agent_service.test_application import (
    NOW as APP_NOW,
)
from tests.marketing.agent_service.test_application import (
    EffectThenStopReasoning,
    ResearchAdapter,
    _descriptor,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.agent_service.test_http_api import StopReasoning

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.marketing.channels.slack_commands import SlackCommands
    from ads_booster.transport.json_types import JsonObject

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def setup_commands(root: Path) -> SlackCommands:
    path = root / "slack.json"
    _ = path.write_text(
        json.dumps(
            {
                "app_id": "A1",
                "team_id": "T1",
                "tenant_id": "team",
                "members": [{"slack_user_id": "U1", "member_id": "member", "can_approve": True}],
            }
        )
    )
    db = root / "agent.sqlite3"
    service = MarketingAgentService(
        SqliteAgentRunRepository(db), ToolRegistry(()), StopReasoning(), {}, SqliteSessionStore(db)
    )
    commands = slack_from_env(
        {
            "TRACE_MARKETING_SLACK_INSTALLATION": str(path),
            "TRACE_MARKETING_SLACK_SIGNING_SECRET": "test-secret",
            "TRACE_MARKETING_PUBLIC_ORIGIN": "https://agent.example",
            "TRACE_MARKETING_SLACK_BOT_TOKEN": "fake",
            "TRACE_MARKETING_SLACK_CHANNEL_ID": "C1",
        },
        service,
    )
    assert commands is not None
    return commands


def request(text: str = "조사해줘", **overrides: str) -> tuple[bytes, dict[str, str]]:
    body = urlencode(
        {
            "api_app_id": "A1",
            "team_id": "T1",
            "channel_id": "C1",
            "user_id": "U1",
            "command": "/trace",
            "trigger_id": "request1",
            "text": text,
            **overrides,
        }
    ).encode()
    timestamp = str(int(NOW.timestamp()))
    return body, {
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": slack_signature(b"test-secret", body, timestamp),
    }


def test_real_slash_request_acks_before_reasoning_and_survives_restart(tmp_path: Path) -> None:
    owner = setup_commands(tmp_path)
    body, headers = request()
    accepted = owner.receive(body, headers, now=NOW)
    assert accepted["response_type"] == "ephemeral"
    assert owner.application.service.repository.list_runs("team") == ()
    restarted = setup_commands(tmp_path)
    messages: list[JsonObject] = []

    def send(payload: JsonObject) -> JsonObject:
        messages.append(payload)
        return {"ok": True, "ts": "123.456"}

    restarted.sender = send
    restarted.recover()
    assert restarted.work_once(now=NOW)
    assert len(restarted.application.service.repository.list_runs("team")) == 1
    assert len(messages) == 1
    assert restarted.receive(body, headers, now=NOW) == accepted
    assert not restarted.work_once(now=NOW)
    assert len(messages) == 1


@pytest.mark.parametrize(
    "fields", [{"team_id": "T2"}, {"channel_id": "C2"}, {"user_id": "U2"}, {"api_app_id": "A2"}]
)
def test_signed_request_still_requires_installation_channel_and_member(
    tmp_path: Path, fields: dict[str, str]
) -> None:
    owner = setup_commands(tmp_path)
    body, headers = request(**fields)
    with pytest.raises(ValueError, match=r"scope_rejected|identity not bound"):
        _ = owner.receive(body, headers, now=NOW)
    assert owner.application.service.repository.list_runs("team") == ()


def test_forged_request_and_ambiguous_notification_never_dispatch_again(tmp_path: Path) -> None:
    owner = setup_commands(tmp_path)
    body, headers = request()
    with pytest.raises(ValueError, match="signature"):
        _ = owner.receive(body + b"x", headers, now=NOW)
    _ = owner.receive(body, headers, now=NOW)
    calls: list[JsonObject] = []

    def lost(payload: JsonObject) -> JsonObject:
        calls.append(payload)
        raise TimeoutError

    owner.sender = lost
    assert owner.work_once(now=NOW)
    owner.recover()
    assert not owner.work_once(now=NOW)
    assert len(calls) == 1


def test_slack_review_exposes_exact_invocation_without_requiring_web_login(tmp_path: Path) -> None:
    owner = setup_commands(tmp_path)
    service = owner.application.service
    service.registry = ToolRegistry(
        (_descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=True),)
    )
    service.reasoning = EffectThenStopReasoning()
    service.tools = {"capture.appium": ResearchAdapter()}
    _ = service.create(_request().model_copy(update={"tenant_id": "team"}), now=APP_NOW)
    body, headers = request("review run-one 1")
    result = owner.receive(body, headers, now=NOW)
    assert isinstance(result["text"], str)
    invocation = ToolInvocation.model_validate_json(result["text"].split("\n", 3)[3])
    assert f"승인해시: {contract_sha256(invocation)}" in result["text"]
    owner.public_links = False
    assert "https://agent.example" not in owner.summary("team", "run-one")
    body, headers = request("review run-one 999")
    with pytest.raises(ValueError, match="agent_review_page_invalid"):
        _ = owner.receive(body, headers, now=NOW)

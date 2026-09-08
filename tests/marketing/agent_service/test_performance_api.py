"""Authenticated same-work readback keeps human observations distinct from live metrics."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.marketing.agent_service.http_api import MarketingAgentApi
from ads_booster.marketing.agent_service.oauth import OAuthIdentity
from ads_booster.marketing.channels.slack_conversations import Conversation
from tests.marketing.channels.test_slack_commands import NOW
from tests.marketing.channels.test_slack_events import receive, setup_events
from tests.marketing.channels.test_slack_performance import payload

if TYPE_CHECKING:
    from pathlib import Path


def test_signed_report_is_readable_only_in_authenticated_run_scope(tmp_path: Path) -> None:
    events, _ = setup_events(tmp_path)
    receive(events)
    assert events.work_once(now=NOW)
    receive(
        events, type="message", text="성과 기록 " + payload(), ts="100.002", thread_ts="100.001"
    )
    assert events.work_once(now=NOW)
    service = events.commands.application.service
    run = service.repository.list_runs("team")[0]
    api = MarketingAgentApi(
        service=service,
        bearer_token="fixture-token",  # noqa: S106 - fixture only
        tenant_id="team",
        principal_id="member",
    )
    path = f"/v1/runs/{run.run_id}/performance"
    assert api.dispatch("GET", path, authorization=None).status == 401
    response = api.dispatch("GET", path, authorization="Bearer fixture-token")
    assert response.status == 200
    assert isinstance(response.body, dict)
    assert response.body["evidence_status"] == "human_reported"
    observations = response.body["observations"]
    assert isinstance(observations, list)
    assert len(observations) == 1
    assert isinstance(observations[0], dict)
    assert observations[0]["views"] == 100
    assert observations[0]["installs"] is None
    other = replace(api, tenant_id="other")
    assert other.dispatch("GET", path, authorization="Bearer fixture-token").status == 404
    assert (
        api.dispatch(
            "GET", "/v1/runs/missing/performance", authorization="Bearer fixture-token"
        ).status
        == 404
    )
    assert (
        api.dispatch(
            "GET", path + "?workspace_id=other", authorization="Bearer fixture-token"
        ).status
        == 400
    )
    assert (
        api.dispatch("POST", path, authorization="Bearer fixture-token", body=b"{}").status == 405
    )


class Authenticator:
    def authenticate(self, authorization: str | None) -> OAuthIdentity | None:
        return OAuthIdentity("team", "reader") if authorization == "Bearer reader-token" else None


def test_oauth_reader_and_private_chat_boundary(tmp_path: Path) -> None:
    events, _ = setup_events(tmp_path)
    receive(events)
    assert events.work_once(now=NOW)
    service = events.commands.application.service
    run = service.repository.list_runs("team")[0]
    api = MarketingAgentApi(
        service=service,
        bearer_token="",
        tenant_id="team",
        principal_id="member",
        oauth_authenticator=Authenticator(),
    )
    response = api.dispatch(
        "GET", f"/v1/runs/{run.run_id}/performance", authorization="Bearer reader-token"
    )
    assert response.status == 200
    assert isinstance(response.body, dict)
    assert response.body["observations"] == []
    receive(
        events, type="message", channel="D1", channel_type="im", text="개인 성과 정리", ts="101.001"
    )
    assert events.work_once(now=NOW)
    with events.store.connect() as db:
        raw = TypeAdapter(tuple[str]).validate_python(
            db.execute(
                """SELECT data_json FROM slack_conversations
                WHERE json_extract(data_json,'$.private')=1"""
            ).fetchone()
        )
        conversation = Conversation.model_validate_json(str(raw[0]))
    private_api = MarketingAgentApi(
        service=events.private_service,
        bearer_token="private-token",  # noqa: S106 - fixture only
        tenant_id=conversation.tenant_id,
        principal_id="member",
    )
    assert (
        private_api.dispatch(
            "GET",
            f"/v1/runs/{conversation.current_run}/performance",
            authorization="Bearer private-token",
        ).status
        == 403
    )

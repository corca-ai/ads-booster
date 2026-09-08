from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest

from ads_booster.contracts.agent_run import AgentRecordKind, AgentRunState, contract_sha256
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.marketing.agent_core.registry import ToolRegistry
from ads_booster.marketing.agent_service.http_api import MarketingAgentApi
from ads_booster.marketing.agent_service.jobs import AgentJobs, WebJob
from ads_booster.marketing.agent_service.oauth import OAuthIdentity
from tests.marketing.agent_service.test_application import (
    NOW,
    AskThenStopReasoning,
    EffectThenStopReasoning,
    ResearchAdapter,
    _descriptor,  # pyright: ignore[reportPrivateUsage]
    _request,  # pyright: ignore[reportPrivateUsage]
    _service,  # pyright: ignore[reportPrivateUsage]
)
from tests.marketing.agent_service.test_browser_login import login

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


class MemberAuthenticator:
    def authenticate(self, authorization: str | None) -> OAuthIdentity | None:
        return OAuthIdentity("trace", "member") if authorization == "Bearer fixture" else None


def pending_api(
    root: Path, *, oauth: bool
) -> tuple[MarketingAgentApi, ResearchAdapter, JsonObject]:
    adapter = ResearchAdapter()
    service = _service(root / "state.db", AskThenStopReasoning())
    service.reasoning = EffectThenStopReasoning()
    service.registry = ToolRegistry(
        (_descriptor("capture.appium", EffectClass.LOCAL_ARTIFACT, ready=True),)
    )
    service.tools = {"capture.appium": adapter}
    run = service.create(_request(), now=NOW)
    assert run.state is AgentRunState.AWAITING_APPROVAL
    invocation = next(
        record
        for record in reversed(service.repository.records("trace", run.run_id))
        if record.kind is AgentRecordKind.INVOCATION
    )
    body: JsonObject = {
        "invocation_sha256": contract_sha256(invocation.payload),
        "decision": "granted",
        "expires_at": (NOW + timedelta(minutes=1)).isoformat(),
    }
    return (
        MarketingAgentApi(
            service,
            "trace",
            "local-operator",
            "fixture",
            oauth_authenticator=MemberAuthenticator() if oauth else None,
            jobs=AgentJobs(service),
        ),
        adapter,
        body,
    )


@pytest.mark.parametrize("queued", [False, True])
def test_authenticated_oauth_member_cannot_approve_or_enqueue_effect(
    tmp_path: Path,
    queued: bool,
) -> None:
    api, adapter, body = pending_api(tmp_path, oauth=True)
    path = "/v1/runs/run-one/approval"
    if queued:
        path = "/v1/jobs"
        body.update({"job_id": "approval-1", "run_id": "run-one", "action": "approval"})
    result = api.dispatch(
        "POST", path, authorization="Bearer fixture", body=json.dumps(body).encode(), now=NOW
    )
    assert result.status == 403
    assert result.body == {"error": "agent_approval_permission_required"}
    assert adapter.inputs == []
    run = api.service.repository.get("trace", "run-one")
    assert run is not None
    assert run.state is AgentRunState.AWAITING_APPROVAL
    assert api.jobs is not None
    assert not api.jobs.work_once(now=NOW)


@pytest.mark.parametrize("queued", [False, True])
def test_configured_local_operator_retains_explicit_approval_authority(
    tmp_path: Path,
    queued: bool,
) -> None:
    api, adapter, body = pending_api(tmp_path, oauth=False)
    path = "/v1/runs/run-one/approval"
    if queued:
        path = "/v1/jobs"
        body.update({"job_id": "approval-1", "run_id": "run-one", "action": "approval"})
    result = api.dispatch(
        "POST", path, authorization="Bearer fixture", body=json.dumps(body).encode(), now=NOW
    )
    assert result.status == 202
    if queued:
        assert api.jobs is not None
        assert api.jobs.work_once(now=NOW)
    assert len(adapter.inputs) == 1


def test_trusted_server_authorizer_can_grant_exact_oauth_membership(tmp_path: Path) -> None:
    api, adapter, body = pending_api(tmp_path, oauth=True)
    checked: list[OAuthIdentity] = []

    def authorize(identity: OAuthIdentity) -> bool:
        checked.append(identity)
        return identity == OAuthIdentity("trace", "member")

    api = replace(api, approval_authorizer=authorize)
    result = api.dispatch(
        "POST",
        "/v1/runs/run-one/approval",
        authorization="Bearer fixture",
        body=json.dumps(body).encode(),
        now=NOW,
    )
    assert result.status == 202
    assert checked == [OAuthIdentity("trace", "member")]
    assert len(adapter.inputs) == 1


def test_request_body_cannot_supply_reviewer_authority(tmp_path: Path) -> None:
    api, adapter, body = pending_api(tmp_path, oauth=True)
    body["can_approve"] = True
    body["approver_id"] = "local-operator"
    result = api.dispatch(
        "POST",
        "/v1/runs/run-one/approval",
        authorization="Bearer fixture",
        body=json.dumps(body).encode(),
        now=NOW,
    )
    assert result.status in {403, 409}
    assert adapter.inputs == []


@pytest.mark.parametrize("queued", [False, True])
def test_browser_member_with_valid_session_and_csrf_is_not_a_reviewer(
    tmp_path: Path,
    queued: bool,
) -> None:
    api, adapter, body = pending_api(tmp_path, oauth=False)
    owner = login()
    owner.authenticator = MemberAuthenticator()
    owner.exchange = lambda _url, _form, _client, _secret: {
        "access_token": "fixture",
        "token_type": "Bearer",
        "expires_in": 120,
    }
    url, cookie = owner.start()
    state = parse_qs(urlsplit(url).query)["state"][0]
    session_cookie = owner.callback(state, "fixture-code", cookie)
    session = owner.session(session_cookie)
    assert session is not None
    api = replace(api, browser_login=owner)
    path = "/v1/runs/run-one/approval"
    if queued:
        path = "/v1/jobs"
        body.update({"job_id": "approval-1", "run_id": "run-one", "action": "approval"})
    result = api.dispatch(
        "POST",
        path,
        authorization=None,
        body=json.dumps(body).encode(),
        now=NOW,
        headers={
            "cookie": session_cookie,
            "origin": "https://agent.example",
            "x-trace-csrf": session.csrf,
        },
    )
    assert result.status == 403
    assert result.body == {"error": "agent_approval_permission_required"}
    assert adapter.inputs == []


def test_unavailable_trusted_role_lookup_fails_closed(tmp_path: Path) -> None:
    api, adapter, body = pending_api(tmp_path, oauth=True)

    def unavailable(identity: OAuthIdentity) -> bool:
        _ = identity
        message = "fixture private directory lookup error"
        raise OSError(message)

    api = replace(api, approval_authorizer=unavailable)
    result = api.dispatch(
        "POST",
        "/v1/runs/run-one/approval",
        authorization="Bearer fixture",
        body=json.dumps(body).encode(),
        now=NOW,
    )
    assert result.status == 403
    assert result.body == {"error": "agent_approval_permission_required"}
    assert adapter.inputs == []


def test_queued_approval_rechecks_revoked_membership_before_dispatch(tmp_path: Path) -> None:
    api, adapter, body = pending_api(tmp_path, oauth=True)
    allowed = [True]

    def authorize(identity: OAuthIdentity) -> bool:
        assert identity == OAuthIdentity("trace", "member")
        return allowed[0]

    api = replace(api, approval_authorizer=authorize)
    body.update({"job_id": "approval-1", "run_id": "run-one", "action": "approval"})
    result = api.dispatch(
        "POST",
        "/v1/jobs",
        authorization="Bearer fixture",
        body=json.dumps(body).encode(),
        now=NOW,
    )
    assert result.status == 202
    allowed[0] = False
    assert api.jobs is not None
    assert api.jobs.work_once(now=NOW)
    status = api.jobs.status("trace", "approval-1")
    assert status["state"] == "blocked"
    assert status["error"] == "agent_approval_permission_required"
    assert adapter.inputs == []
    assert not api.jobs.work_once(now=NOW)


def test_legacy_pending_approval_without_trusted_authorizer_is_blocked(tmp_path: Path) -> None:
    api, adapter, body = pending_api(tmp_path, oauth=True)
    legacy = AgentJobs(api.service)
    body.update({"job_id": "legacy-approval", "run_id": "run-one", "action": "approval"})
    _ = legacy.enqueue("trace", "member", WebJob.model_validate(body))
    restarted = AgentJobs(api.service)
    restarted.recover()
    assert restarted.work_once(now=NOW)
    status = restarted.status("trace", "legacy-approval")
    assert status["state"] == "blocked"
    assert status["error"] == "agent_approval_permission_required"
    assert adapter.inputs == []

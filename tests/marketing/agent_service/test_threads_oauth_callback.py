from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx2
import pytest
from pydantic import TypeAdapter

from ads_booster.channels.http.http_api import MarketingAgentApi
from ads_booster.providers.threads_api import ThreadsApiClient
from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault
from ads_booster.threads.effect_fence import ThreadsEffectFence
from ads_booster.threads.oauth import ThreadsOAuthService
from ads_booster.threads.oauth_diagnostics import OAuthDiagnosticEvent
from tests.marketing.agent_service.test_application import AskThenStopReasoning, build_service

if TYPE_CHECKING:
    from pathlib import Path


def assert_callback_diagnostic(
    caplog: pytest.LogCaptureFixture, failed_path: str, provider_code: int | None, state_id: str
) -> OAuthDiagnosticEvent:
    diagnostic_records = [
        r for r in caplog.records if r.name == "ads_booster.threads.oauth_diagnostics"
    ]
    assert len(diagnostic_records) == 1
    diagnostic = TypeAdapter(OAuthDiagnosticEvent).validate_json(diagnostic_records[0].getMessage())
    stages = [
        "fence",
        "consume_state",
        "exchange_code",
        "exchange_long_lived",
        "user",
        "granted_scopes",
        "validate_scopes",
        "persist_account",
    ]
    stage = {
        "/oauth/access_token": "exchange_code",
        "/access_token": "exchange_long_lived",
        "/v1.0/me": "user",
        "/debug_token": "granted_scopes",
    }[failed_path]
    assert diagnostic["stage"] == (stage if provider_code is not None else "persist_account")
    assert diagnostic["event"] == (
        "threads_oauth_failed" if provider_code is not None else "threads_oauth_completed"
    )
    assert diagnostic["completed_stages"] == (
        stages[: stages.index(stage)] if provider_code is not None else stages
    )
    assert diagnostic["meta_code"] == provider_code
    assert diagnostic["meta_subcode"] == (12345 if provider_code is not None else None)
    assert diagnostic["http_status"] == (400 if provider_code is not None else None)
    assert diagnostic["elapsed_ms"] >= 0
    assert diagnostic["attempt_id"]
    assert diagnostic_records[0].exc_info is None
    for sensitive in (
        "fixture-code",
        "app-secret",
        "sensitive-provider-detail",
        state_id,
        "fixture_account",
        "fixture-short-token-secret",
        "fixture-long-token-secret",
    ):
        assert sensitive not in diagnostic_records[0].getMessage()
    return diagnostic


def assert_replay_diagnostic(
    caplog: pytest.LogCaptureFixture, previous: OAuthDiagnosticEvent
) -> None:
    records = [r for r in caplog.records if r.name == "ads_booster.threads.oauth_diagnostics"]
    assert len(records) == 2
    event = TypeAdapter(OAuthDiagnosticEvent).validate_json(records[-1].getMessage())
    assert event["stage"] == "consume_state"
    assert event["attempt_id"] != previous["attempt_id"]


@pytest.mark.parametrize(
    ("failed_path", "provider_code"),
    [
        ("/oauth/access_token", None),
        ("/oauth/access_token", 190),
        ("/access_token", 190),
        ("/v1.0/me", 190),
        ("/debug_token", 190),
        ("/access_token", 1349245),
    ],
)
def test_callback_persists_usable_account_or_returns_actionable_error(
    tmp_path: Path, provider_code: int | None, failed_path: str, caplog: pytest.LogCaptureFixture
) -> None:
    # Given the real callback, OAuth, provider parser, account repository and token vault.
    now = datetime.now(UTC)
    calls: list[str] = []
    caplog.set_level(logging.INFO, logger="ads_booster.threads.oauth_diagnostics")

    def provider(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if request.url.path == failed_path and provider_code is not None:
            return httpx2.Response(
                400,
                json={
                    "error": {
                        "code": provider_code,
                        "error_subcode": 12345,
                        "message": "sensitive-provider-detail fixture-code app-secret short long",
                    }
                },
            )
        if request.url.path == "/oauth/access_token":
            return httpx2.Response(
                200, json={"access_token": "fixture-short-token-secret", "user_id": "123"}
            )
        if request.url.path == "/access_token":
            return httpx2.Response(
                200, json={"access_token": "fixture-long-token-secret", "expires_in": 5184000}
            )
        if request.url.path == "/debug_token":
            return httpx2.Response(
                200,
                json={
                    "data": {
                        "is_valid": True,
                        "user_id": "123",
                        "scopes": ["threads_basic"],
                    }
                },
            )
        assert request.url.path == "/v1.0/me"
        assert request.headers["Authorization"] == "Bearer fixture-long-token-secret"
        return httpx2.Response(200, json={"id": "123", "username": "fixture_account"})

    database = tmp_path / "agent.sqlite3"
    accounts = ThreadsAccountRepository(database)
    vault = ThreadsTokenVault(tmp_path / "tokens")
    with httpx2.Client(
        base_url="https://graph.threads.net", transport=httpx2.MockTransport(provider)
    ) as transport:
        client = ThreadsApiClient("fixture", "app-secret", transport)
        oauth = ThreadsOAuthService(
            str(database),
            "https://agent.example/callback",
            client,
            accounts,
            vault,
            ThreadsEffectFence(database),
        )
        service = build_service(database, AskThenStopReasoning())
        api = MarketingAgentApi(
            service, tenant_id="trace", principal_id="member", bearer_token="", threads_oauth=oauth
        )
        link = oauth.start(
            workspace_id="trace", member_id="member", scopes=("threads_basic",), now=now
        )
        target = "/integrations/threads/callback?" + urlencode(
            {"state": link.state_id, "code": "fixture-code"}
        )
        # When the browser returns from consent.
        response = api.dispatch("GET", target, authorization=None, now=now)
        diagnostic = assert_callback_diagnostic(caplog, failed_path, provider_code, link.state_id)
        # Then failures are controlled responses and success survives repository reconstruction.
        assert isinstance(response.body, dict)
        if provider_code is not None:
            assert response.status == 401
            assert "message" in response.body
            assert "테스트" in str(response.body["message"])
            assert response.body["error"] == (
                "threads_test_invite_required"
                if provider_code == 1349245
                else "threads_token_rejected"
            )
            assert accounts.list_for_workspace("trace") == ()
            assert list(vault.root.iterdir()) == []
        else:
            assert response.status == 200
            assert response.body["connected"] is True
            saved = ThreadsAccountRepository(database).list_for_workspace("trace")
            assert len(saved) == 1
            account = saved[0]
            assert account.owner_member_id == "member"
            assert (vault.root / account.token_ref).stat().st_mode & 0o777 == 0o600
            assert client.user(ThreadsTokenVault(vault.root).get(account.token_ref)).id == "123"
        prior_calls = list(calls)
        replay = api.dispatch("GET", target, authorization=None, now=now)
        assert_replay_diagnostic(caplog, diagnostic)
        assert replay.status == 401
        assert isinstance(replay.body, dict)
        assert "새" in str(replay.body["message"])
        assert calls == prior_calls
        assert "fixture-code" not in str(response.body)
        assert "short" not in str(response.body)
        assert "long" not in str(response.body)

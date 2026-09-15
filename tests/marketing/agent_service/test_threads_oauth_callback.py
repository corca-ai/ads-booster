from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx2
import pytest

from ads_booster.channels.http.http_api import MarketingAgentApi
from ads_booster.providers.threads_api import ThreadsApiClient
from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault
from ads_booster.threads.effect_fence import ThreadsEffectFence
from ads_booster.threads.oauth import ThreadsOAuthService
from tests.marketing.agent_service.test_application import AskThenStopReasoning, build_service

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("provider_code", [None, 1349245, 190])
def test_callback_persists_usable_account_or_returns_actionable_error(
    tmp_path: Path, provider_code: int | None
) -> None:
    # Given the real callback, OAuth, provider parser, account repository and token vault.
    now = datetime.now(UTC)
    calls: list[str] = []

    def provider(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if request.url.path == "/oauth/access_token":
            return httpx2.Response(200, json={"access_token": "short", "user_id": "123"})
        if request.url.path == "/access_token":
            if provider_code is not None:
                return httpx2.Response(
                    400,
                    json={
                        "error": {
                            "code": provider_code,
                            "message": "The user has not accepted the invite to test the app."
                            if provider_code == 1349245
                            else "Session key invalid.",
                        }
                    },
                )
            return httpx2.Response(200, json={"access_token": "long", "expires_in": 5184000})
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
        assert request.headers["Authorization"] == "Bearer long"
        return httpx2.Response(200, json={"id": "123", "username": "fixture_account"})

    database = tmp_path / "agent.sqlite3"
    accounts = ThreadsAccountRepository(database)
    vault = ThreadsTokenVault(tmp_path / "tokens")
    with httpx2.Client(
        base_url="https://graph.threads.net", transport=httpx2.MockTransport(provider)
    ) as transport:
        client = ThreadsApiClient("fixture", "", transport)
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
        assert replay.status == 401
        assert isinstance(replay.body, dict)
        assert "새" in str(replay.body["message"])
        assert calls == prior_calls
        assert "fixture-code" not in str(response.body)
        assert "short" not in str(response.body)
        assert "long" not in str(response.body)

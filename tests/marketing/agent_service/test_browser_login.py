from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest

from ads_booster.marketing.agent_service.browser_login import BrowserLogin, BrowserLoginConfig
from ads_booster.marketing.agent_service.http_api import MarketingAgentApi
from ads_booster.marketing.agent_service.oauth import OAuthIdentity
from tests.marketing.agent_service.test_jobs import jobs

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.transport.json_types import JsonObject


class Auth:
    def authenticate(self, authorization: str | None) -> OAuthIdentity | None:
        return (
            OAuthIdentity("team", "member") if authorization == "Bearer provider-secret" else None
        )


def exchange(url: str, form: dict[str, str], client: str, secret: str) -> JsonObject:
    _ = url, client, secret
    assert form["code_verifier"]
    assert form["redirect_uri"] == "https://agent.example/auth/callback"
    return {"access_token": "provider-secret", "token_type": "Bearer", "expires_in": 120}


def login() -> BrowserLogin:
    return BrowserLogin(
        BrowserLoginConfig(
            "https://agent.example",
            "https://id.example/authorize",
            "https://id.example/token",
            "client",
            "secret",
        ),
        Auth(),
        exchange,
    )


def test_login_binds_callback_to_browser_and_consumes_state() -> None:
    owner = login()
    url, cookie = owner.start()
    query = parse_qs(urlsplit(url).query)
    assert query["code_challenge_method"] == ["S256"]
    session_cookie = owner.callback(query["state"][0], "code", cookie)
    assert "provider-secret" not in session_cookie
    assert "Secure; HttpOnly; SameSite=Lax" in session_cookie
    session = owner.session(session_cookie)
    assert session is not None
    assert owner.identity(session) == OAuthIdentity("team", "member")
    assert owner.mutation_allowed(session, "https://agent.example", session.csrf)
    assert not owner.mutation_allowed(session, "https://evil.example", session.csrf)
    assert not owner.mutation_allowed(session, "https://agent.example", None)
    with pytest.raises(ValueError, match="state_invalid"):
        _ = owner.callback(query["state"][0], "code", cookie)
    _ = owner.logout(session_cookie)
    assert owner.session(session_cookie) is None


def test_login_rejects_wrong_browser_and_expiry() -> None:
    owner = login()
    url, _ = owner.start()
    state = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(ValueError, match="state_invalid"):
        _ = owner.callback(state, "code", None)
    url, cookie = owner.start()
    state = parse_qs(urlsplit(url).query)["state"][0]
    owner.clock = lambda: float("inf")
    with pytest.raises(ValueError, match="state_invalid"):
        _ = owner.callback(state, "code", cookie)


def test_browser_cookie_uses_oauth_identity_and_rejects_cross_origin_write(tmp_path: Path) -> None:

    owner = login()
    url, cookie = owner.start()
    session_cookie = owner.callback(parse_qs(urlsplit(url).query)["state"][0], "code", cookie)
    queue = jobs(tmp_path)
    api = MarketingAgentApi(
        queue.service,
        "unused",
        "unused",
        "",
        oauth_authenticator=Auth(),
        browser_login=owner,
        jobs=queue,
    )
    session = api.dispatch(
        "GET", "/auth/session", authorization=None, headers={"cookie": session_cookie}
    )
    assert isinstance(session.body, dict)
    assert session.body["tenant_id"] == "team"
    rejected = api.dispatch(
        "POST",
        "/v1/jobs",
        authorization=None,
        headers={
            "cookie": session_cookie,
            "origin": "https://evil.example",
            "x-trace-csrf": str(session.body["csrf"]),
        },
        body=b"{}",
    )
    assert rejected.status == 403
    logout = api.dispatch(
        "POST",
        "/auth/logout",
        authorization=None,
        headers={
            "cookie": session_cookie,
            "origin": "https://agent.example",
            "x-trace-csrf": str(session.body["csrf"]),
        },
    )
    assert logout.status == 200
    assert (
        api.dispatch(
            "GET", "/v1/runs", authorization=None, headers={"cookie": session_cookie}
        ).status
        == 401
    )

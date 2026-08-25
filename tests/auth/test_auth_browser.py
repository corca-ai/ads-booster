from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from trace_capture.auth.browser import build_authorization_url


def test_browser_authorization_url_uses_supported_codex_pkce_parameters() -> None:
    url = build_authorization_url(
        verifier="verifier",
        state="state",
        redirect_uri="http://localhost:1455/auth/callback",
        originator="trace-agent",
    )
    query = parse_qs(urlsplit(url).query)

    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid profile email offline_access"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["state"]
    assert query["redirect_uri"] == ["http://localhost:1455/auth/callback"]
    assert query["originator"] == ["trace-agent"]
    assert "model.request" not in query["scope"][0]

from __future__ import annotations

import json
from dataclasses import dataclass

from ads_booster.marketing.agent_service.oauth import OAuthTokenIntrospector


@dataclass
class Response:
    payload: dict[str, object]

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_introspection_binds_subject_and_workspace_claim() -> None:
    seen: list[object] = []

    def open_request(request: object, *, timeout: float) -> Response:
        seen.extend((request, timeout))
        return Response(
            {"active": True, "sub": "member-1", "workspace_id": "team-1", "aud": ["agent"]}
        )

    authenticator = OAuthTokenIntrospector(
        introspection_url="https://identity.example/oauth/introspect",
        client_id="agent-client",
        client_secret="fake-secret",  # noqa: S106
        audience="agent",
        opener=open_request,
    )

    identity = authenticator.authenticate("Bearer opaque-access-token")

    assert identity is not None
    assert (identity.tenant_id, identity.principal_id) == ("team-1", "member-1")
    assert seen[1] == 5.0


def test_introspection_rejects_inactive_wrong_audience_and_non_https() -> None:
    def inactive(_request: object, *, timeout: float) -> Response:
        _ = timeout
        return Response({"active": False, "sub": "member", "workspace_id": "team", "aud": "agent"})

    base = {
        "client_id": "id",
        "client_secret": "fake",
        "audience": "agent",
        "opener": inactive,
    }
    assert OAuthTokenIntrospector(
        introspection_url="https://identity.example/introspect", **base
    ).authenticate("Bearer token") is None
    assert OAuthTokenIntrospector(
        introspection_url="http://identity.example/introspect", **base
    ).authenticate("Bearer token") is None

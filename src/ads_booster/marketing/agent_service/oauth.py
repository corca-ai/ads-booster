"""OAuth 2.0 token-introspection boundary for a remotely hosted Agent Service."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, slots=True)
class OAuthIdentity:
    tenant_id: str
    principal_id: str


class AccessTokenAuthenticator(Protocol):
    def authenticate(self, authorization: str | None) -> OAuthIdentity | None: ...


class IntrospectionResponse(Protocol):
    def read(self) -> bytes: ...


@dataclass(frozen=True, slots=True)
class OAuthTokenIntrospector:
    introspection_url: str
    client_id: str
    client_secret: str
    audience: str
    tenant_claim: str = "workspace_id"
    timeout_seconds: float = 5.0
    opener: Callable[..., IntrospectionResponse] = urlopen

    def authenticate(self, authorization: str | None) -> OAuthIdentity | None:
        token = _bearer_token(authorization)
        if token is None or urlsplit(self.introspection_url).scheme != "https":
            return None
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        request = Request(  # noqa: S310 - URL is operator configuration and HTTPS is required.
            self.introspection_url,
            data=urlencode({"token": token, "token_type_hint": "access_token"}).encode(),
            headers={
                "accept": "application/json",
                "authorization": f"Basic {credentials}",
                "content-type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            response = self.opener(request, timeout=self.timeout_seconds)
            payload = cast("dict[str, object]", json.loads(response.read()))
        except (OSError, ValueError, AttributeError, TypeError):
            return None
        subject = payload.get("sub")
        tenant = payload.get(self.tenant_claim)
        audience = payload.get("aud")
        audiences: set[str]
        if isinstance(audience, str):
            audiences = {audience}
        elif isinstance(audience, list):
            raw_audiences = cast("list[object]", audience)
            audiences = (
                set(cast("list[str]", raw_audiences))
                if all(isinstance(item, str) for item in raw_audiences)
                else set()
            )
        else:
            audiences = set()
        if (
            payload.get("active") is not True
            or not isinstance(subject, str)
            or not subject
            or not isinstance(tenant, str)
            or not tenant
            or self.audience not in audiences
        ):
            return None
        return OAuthIdentity(tenant_id=tenant, principal_id=subject)


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    return token or None


__all__ = ["AccessTokenAuthenticator", "OAuthIdentity", "OAuthTokenIntrospector"]

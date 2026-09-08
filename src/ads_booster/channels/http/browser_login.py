"""Authorization-code/PKCE login; provider tokens never enter browser storage."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from threading import RLock
from time import time
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlsplit

from ads_booster.channels.http.oauth import OAuthIdentity

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.channels.http.oauth import AccessTokenAuthenticator
    from ads_booster.transport.json_types import JsonObject


_MAX_SESSIONS = 1000


def https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("agent_public_origin_invalid")
    return value.rstrip("/")


def cookie_value(raw: str | None, name: str) -> str:
    cookie = SimpleCookie()
    try:
        cookie.load(raw or "")
        return cookie[name].value if name in cookie else ""
    except ValueError:
        return ""


@dataclass(frozen=True, slots=True)
class BrowserLoginConfig:
    public_origin: str
    authorization_url: str
    token_url: str
    client_id: str
    client_secret: str
    scope: str = "openid profile"

    def __post_init__(self) -> None:
        """Pin all redirects and provider endpoints to operator configuration."""
        _ = https_origin(self.public_origin)
        for endpoint in (self.authorization_url, self.token_url):
            parsed = urlsplit(endpoint)
            if parsed.scheme != "https" or not parsed.netloc or parsed.fragment or parsed.username:
                raise ValueError("agent_login_endpoint_invalid")

    @property
    def callback_url(self) -> str:
        return self.public_origin.rstrip("/") + "/auth/callback"


@dataclass(frozen=True, slots=True)
class BrowserSession:
    access_token: str
    csrf: str
    expires_at: float


@dataclass(slots=True)
class BrowserLogin:
    config: BrowserLoginConfig
    authenticator: AccessTokenAuthenticator
    exchange: Callable[[str, dict[str, str], str, str], JsonObject]
    clock: Callable[[], float] = time
    _pending: dict[str, tuple[str, str, float]] = field(default_factory=dict)
    _sessions: dict[str, BrowserSession] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock)

    def start(self) -> tuple[str, str]:
        now = self.clock()
        with self._lock:
            self._pending = {k: v for k, v in self._pending.items() if v[2] > now}
            if len(self._pending) >= _MAX_SESSIONS:
                raise ValueError("agent_login_capacity")
            state, binding, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            self._pending[state] = (binding, verifier, now + 300)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.callback_url,
                "scope": self.config.scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        separator = "&" if "?" in self.config.authorization_url else "?"
        return self.config.authorization_url + separator + query, self._cookie(
            "login", binding, 300
        )

    def callback(self, state: str, code: str, cookies: str | None) -> str:
        with self._lock:
            pending = self._pending.pop(state, None)
        if (
            pending is None
            or pending[2] <= self.clock()
            or not code
            or not secrets.compare_digest(pending[0], cookie_value(cookies, "__Host-trace-login"))
        ):
            raise ValueError("agent_login_state_invalid")
        payload = self.exchange(
            self.config.token_url,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.callback_url,
                "code_verifier": pending[1],
            },
            self.config.client_id,
            self.config.client_secret,
        )
        token_type = payload.get("token_type", "Bearer")
        token, duration = payload.get("access_token"), payload.get("expires_in", 3600)
        if (
            not isinstance(token, str)
            or not isinstance(token_type, str)
            or token_type.lower() != "bearer"
            or not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or duration <= 0
            or self.authenticator.authenticate(f"Bearer {token}") is None
        ):
            raise ValueError("agent_login_token_invalid")
        lifetime = min(int(duration), 3600)
        session_id = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions = {
                k: v for k, v in self._sessions.items() if v.expires_at > self.clock()
            }
            if len(self._sessions) >= _MAX_SESSIONS:
                raise ValueError("agent_login_capacity")
            self._sessions[session_id] = BrowserSession(
                token, secrets.token_urlsafe(32), self.clock() + lifetime
            )
        return self._cookie("session", session_id, lifetime)

    def session(self, cookies: str | None) -> BrowserSession | None:
        key = cookie_value(cookies, "__Host-trace-session")
        with self._lock:
            session = self._sessions.get(key)
            if session is not None and session.expires_at <= self.clock():
                del self._sessions[key]
                return None
            return session

    def identity(self, session: BrowserSession) -> OAuthIdentity | None:
        return self.authenticator.authenticate(f"Bearer {session.access_token}")

    def mutation_allowed(
        self, session: BrowserSession, origin: str | None, csrf: str | None
    ) -> bool:
        return origin == self.config.public_origin.rstrip("/") and secrets.compare_digest(
            session.csrf, csrf or ""
        )

    def logout(self, cookies: str | None) -> str:
        with self._lock:
            _ = self._sessions.pop(cookie_value(cookies, "__Host-trace-session"), None)
        return self._cookie("session", "", 0)

    @staticmethod
    def _cookie(name: str, value: str, age: int) -> str:
        return f"__Host-trace-{name}={value}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age={age}"

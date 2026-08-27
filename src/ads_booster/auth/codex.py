from __future__ import annotations

import base64
import binascii
import time
import webbrowser
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, override

import httpx2
from pydantic import TypeAdapter, ValidationError

from ads_booster.auth.browser import (
    BrowserOAuthOptions,
    run_browser_oauth,
)
from ads_booster.auth.models import (
    DeviceCodePayload,
    DeviceTokenPayload,
    OAuthCredential,
    OAuthTokenPayload,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from collections.abc import Callable

    from ads_booster.auth.store import AuthStore
    from ads_booster.transport.http import HttpClient

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_ISSUER = "https://auth.openai.com"
CODEX_TOKEN_URL = f"{CODEX_ISSUER}/oauth/token"
CODEX_BACKEND_URL = "https://chatgpt.com/backend-api/codex"
CODEX_DEVICE_URL = f"{CODEX_ISSUER}/codex/device"
HTTP_OK: Final = 200
JWT_PART_COUNT: Final = 3
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class OAuthError(RuntimeError):
    code: str
    message: str

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True, slots=True)
class OAuthLoginOptions:
    open_browser: bool = True
    timeout_seconds: float = 900.0


@dataclass(frozen=True, slots=True)
class DeviceChallenge:
    verification_url: str
    user_code: str


@dataclass(frozen=True, slots=True)
class CodexOAuth:
    http: HttpClient
    store: AuthStore
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time

    def login(
        self,
        options: OAuthLoginOptions | None = None,
        on_challenge: Callable[[DeviceChallenge], None] | None = None,
    ) -> OAuthCredential:
        login_options = options or OAuthLoginOptions()
        device = self._request_device_code()
        challenge = DeviceChallenge(CODEX_DEVICE_URL, device.user_code)
        if on_challenge is not None:
            on_challenge(challenge)
        if login_options.open_browser:
            _ = webbrowser.open(challenge.verification_url)
        token = self._poll_device_token(device, login_options.timeout_seconds)
        credential = self._exchange_token(token)
        self.store.save(credential)
        return credential

    def login_browser(
        self,
        options: BrowserOAuthOptions | None = None,
        on_auth: Callable[[str], None] | None = None,
    ) -> OAuthCredential:
        authorization = run_browser_oauth(
            originator="trace-agent",
            options=options or BrowserOAuthOptions(),
            on_auth=on_auth,
        )
        credential = self._exchange_authorization_code(
            authorization.code,
            authorization.verifier,
            authorization.redirect_uri,
        )
        self.store.save(credential)
        return credential

    def refresh_if_needed(self, now: float | None = None) -> OAuthCredential:
        credential = self.store.load()
        if credential is None:
            msg = "auth_missing"
            raise OAuthError(msg, "OpenAI OAuth login is required")
        current_time = self.clock() if now is None else now
        if credential.expires_at > current_time + 120:
            return credential
        refreshed = self._refresh(credential)
        self.store.save(refreshed)
        return refreshed

    def logout(self) -> None:
        self.store.clear()

    def _request_device_code(self) -> DeviceCodePayload:
        try:
            response = self.http.post_json(
                f"{CODEX_ISSUER}/api/accounts/deviceauth/usercode",
                {"client_id": CODEX_CLIENT_ID},
                {"Content-Type": "application/json"},
            )
        except httpx2.HTTPError as error:
            msg = "device_code_request_failed"
            raise OAuthError(msg, "OpenAI device login request failed") from error
        if response.status_code != HTTP_OK:
            msg = "device_code_request_failed"
            raise OAuthError(msg, f"OpenAI returned HTTP {response.status_code}")
        try:
            return DeviceCodePayload.model_validate(response.json_object())
        except ValidationError as error:
            msg = "device_code_invalid"
            raise OAuthError(msg, "OpenAI returned an invalid device login response") from error

    def _poll_device_token(
        self, device: DeviceCodePayload, timeout_seconds: float
    ) -> DeviceTokenPayload:
        deadline = self.clock() + timeout_seconds
        while self.clock() < deadline:
            self.sleep(device.interval)
            try:
                response = self.http.post_json(
                    f"{CODEX_ISSUER}/api/accounts/deviceauth/token",
                    {"device_auth_id": device.device_auth_id, "user_code": device.user_code},
                    {"Content-Type": "application/json"},
                )
            except httpx2.HTTPError as error:
                msg = "device_code_poll_failed"
                raise OAuthError(msg, "OpenAI device login polling failed") from error
            if response.status_code in {403, 404}:
                continue
            if response.status_code != HTTP_OK:
                msg = "device_code_poll_failed"
                raise OAuthError(msg, f"OpenAI returned HTTP {response.status_code}")
            try:
                return DeviceTokenPayload.model_validate(response.json_object())
            except ValidationError as error:
                msg = "device_code_invalid"
                raise OAuthError(
                    msg, "OpenAI returned an invalid authorization response"
                ) from error
        msg = "device_code_timeout"
        raise OAuthError(msg, "OpenAI device login timed out")

    def _exchange_token(self, token: DeviceTokenPayload) -> OAuthCredential:
        return self._exchange_authorization_code(
            token.authorization_code,
            token.code_verifier,
            f"{CODEX_ISSUER}/deviceauth/callback",
        )

    def _exchange_authorization_code(
        self,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> OAuthCredential:
        try:
            response = self.http.post_form(
                CODEX_TOKEN_URL,
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": CODEX_CLIENT_ID,
                    "code_verifier": verifier,
                },
                {"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx2.HTTPError as error:
            msg = "token_exchange_failed"
            raise OAuthError(msg, "OpenAI OAuth token exchange failed") from error
        if response.status_code != HTTP_OK:
            msg = "token_exchange_failed"
            raise OAuthError(msg, f"OpenAI returned HTTP {response.status_code}")
        try:
            payload = OAuthTokenPayload.model_validate(response.json_object())
        except ValidationError as error:
            msg = "token_invalid"
            raise OAuthError(msg, "OpenAI returned an invalid OAuth token response") from error
        return _credential_from_payload(payload, self.clock())

    def _refresh(self, credential: OAuthCredential) -> OAuthCredential:
        try:
            response = self.http.post_form(
                CODEX_TOKEN_URL,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": credential.refresh_token,
                    "client_id": CODEX_CLIENT_ID,
                },
                {"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx2.HTTPError as error:
            msg = "refresh_failed"
            raise OAuthError(msg, "OpenAI OAuth token refresh failed") from error
        if response.status_code != HTTP_OK:
            msg = "refresh_failed"
            raise OAuthError(msg, f"OpenAI returned HTTP {response.status_code}")
        try:
            payload = OAuthTokenPayload.model_validate(response.json_object())
        except ValidationError as error:
            msg = "refresh_invalid"
            raise OAuthError(msg, "OpenAI returned an invalid refresh response") from error
        return _credential_from_payload(
            payload,
            self.clock(),
            refresh_token=payload.refresh_token or credential.refresh_token,
            account_id=credential.account_id,
        )


def _credential_from_payload(
    payload: OAuthTokenPayload,
    now: float,
    *,
    refresh_token: str | None = None,
    account_id: str | None = None,
) -> OAuthCredential:
    resolved_refresh_token = refresh_token or payload.refresh_token
    if not resolved_refresh_token:
        msg = "token_missing_refresh"
        raise OAuthError(msg, "OpenAI OAuth response did not include a refresh token")
    return OAuthCredential(
        access_token=payload.access_token,
        refresh_token=resolved_refresh_token,
        expires_at=now + payload.expires_in,
        account_id=account_id or _account_id_from_token(payload.access_token),
        token_type=payload.token_type,
    )


def _account_id_from_token(access_token: str) -> str | None:
    parts = access_token.split(".")
    if len(parts) != JWT_PART_COUNT:
        return None
    try:
        decoded = base64.urlsafe_b64decode(f"{parts[1]}===")
        payload = _JSON_OBJECT.validate_json(decoded)
        for key in ("chatgpt_account_id", "account_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        auth_payload = payload.get("https://api.openai.com/auth")
        if isinstance(auth_payload, dict):
            value = auth_payload.get("chatgpt_account_id")
            if isinstance(value, str) and value:
                return value
    except binascii.Error, UnicodeDecodeError, ValidationError:
        return None
    return None

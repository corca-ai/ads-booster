from __future__ import annotations

import base64
import hashlib
import html
import queue
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, override
from urllib.parse import parse_qs, urlencode, urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable

CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
CALLBACK_PATH = "/auth/callback"


class BrowserOAuthError(RuntimeError):
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
class OAuthCallback:
    code: str | None
    state: str | None
    error: str | None
    error_description: str | None


@dataclass(frozen=True, slots=True)
class OAuthAuthorization:
    code: str
    verifier: str
    redirect_uri: str


@dataclass(frozen=True, slots=True)
class BrowserOAuthOptions:
    open_browser: bool = True
    timeout_seconds: float = 300.0
    callback_host: str = "localhost"
    callback_port: int = 1455
    input_fn: Callable[[str], str] | None = None


@dataclass(slots=True)
class CallbackWaiter:
    server: HTTPServer
    thread: threading.Thread
    results: queue.Queue[OAuthCallback]

    @classmethod
    def start(cls, host: str, port: int, expected_path: str) -> CallbackWaiter:
        results: queue.Queue[OAuthCallback] = queue.Queue()
        handler = _handler_for(results, expected_path)
        server = HTTPServer((host, port), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return cls(server=server, thread=thread, results=results)

    def wait(self, timeout_seconds: float) -> OAuthCallback | None:
        try:
            return self.results.get(timeout=timeout_seconds)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()


def build_authorization_url(
    *, verifier: str, state: str, redirect_uri: str, originator: str
) -> str:
    challenge = _code_challenge(verifier)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": CODEX_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email offline_access",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": originator,
        }
    )
    return f"{CODEX_AUTHORIZE_URL}?{query}"


def run_browser_oauth(
    *,
    originator: str,
    options: BrowserOAuthOptions,
    on_auth: Callable[[str], None] | None = None,
) -> OAuthAuthorization:
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_hex(16)
    redirect_uri = _redirect_uri(options.callback_host, options.callback_port)
    url = build_authorization_url(
        verifier=verifier,
        state=state,
        redirect_uri=redirect_uri,
        originator=originator,
    )
    if on_auth is not None:
        on_auth(url)
    waiter: CallbackWaiter | None = None
    try:
        try:
            waiter = CallbackWaiter.start(
                options.callback_host,
                options.callback_port,
                CALLBACK_PATH,
            )
        except OSError:
            waiter = None
        if options.open_browser:
            _ = webbrowser.open(url)
        callback = waiter.wait(options.timeout_seconds) if waiter is not None else None
    finally:
        if waiter is not None:
            waiter.close()
    if callback is None:
        if options.input_fn is None:
            msg = "callback_unavailable"
            raise BrowserOAuthError(msg, "OAuth callback was not received")
        callback = _parse_callback_input(options.input_fn("Paste the OAuth redirect URL: "))
    if callback.error is not None:
        detail = callback.error_description or callback.error
        msg = "oauth_denied"
        raise BrowserOAuthError(msg, detail)
    if callback.state != state:
        msg = "state_mismatch"
        raise BrowserOAuthError(msg, "OAuth callback state did not match")
    if callback.code is None:
        msg = "code_missing"
        raise BrowserOAuthError(msg, "OAuth callback did not contain an authorization code")
    return OAuthAuthorization(code=callback.code, verifier=verifier, redirect_uri=redirect_uri)


def _redirect_uri(host: str, port: int) -> str:
    if host not in {"localhost", "127.0.0.1", "::1"}:
        msg = "callback_host_invalid"
        raise BrowserOAuthError(msg, "OAuth callback host must be loopback")
    display_host = f"[{host}]" if host == "::1" else host
    return f"http://{display_host}:{port}{CALLBACK_PATH}"


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _parse_callback_input(value: str) -> OAuthCallback:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        msg = "callback_invalid"
        raise BrowserOAuthError(msg, "Paste the full OAuth redirect URL")
    return _callback_from_query(parse_qs(parsed.query))


def _handler_for(
    results: queue.Queue[OAuthCallback], expected_path: str
) -> type[BaseHTTPRequestHandler]:
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path != expected_path:
                _ = self.send_error(404)
                return
            callback = _callback_from_query(parse_qs(parsed.query))
            results.put(callback)
            message = "OAuth complete. You can close this window."
            if callback.error is not None:
                message = f"OAuth failed: {callback.error}"
            body = f"<html><body>{html.escape(message)}</body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            _ = self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: str) -> None:
            _ = (format, args)

    return CallbackHandler


def _callback_from_query(query: dict[str, list[str]]) -> OAuthCallback:
    return OAuthCallback(
        code=_first(query, "code"),
        state=_first(query, "state"),
        error=_first(query, "error"),
        error_description=_first(query, "error_description"),
    )


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key, [])
    return values[0] if values else None

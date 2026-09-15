from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qs

from ads_booster.threads.callback_urls import (
    DATA_DELETION_CALLBACK_PATH,
    DATA_DELETION_STATUS_PREFIX,
    DEAUTHORIZE_CALLBACK_PATH,
)
from ads_booster.threads.provider_callbacks import ThreadsProviderCallbackError

if TYPE_CHECKING:
    from ads_booster.threads.privacy import ThreadsPrivacyCallbacks
    from ads_booster.transport.json_types import JsonObject

_MAX_BODY_BYTES: Final = 1024 * 1024
_INVALID_CALLBACK: Final = "threads_callback_invalid"


def dispatch_threads_privacy(
    method: str,
    path: str,
    body: bytes,
    *,
    now: datetime | None,
    callbacks: ThreadsPrivacyCallbacks | None,
) -> tuple[int, JsonObject] | None:
    if method == "POST" and path in {
        DEAUTHORIZE_CALLBACK_PATH,
        DATA_DELETION_CALLBACK_PATH,
    }:
        return _dispatch_post(path, body, now=now, callbacks=callbacks)
    if method == "GET" and path.startswith(DATA_DELETION_STATUS_PREFIX):
        return _dispatch_status(path, callbacks)
    return None


def _dispatch_post(
    path: str,
    body: bytes,
    *,
    now: datetime | None,
    callbacks: ThreadsPrivacyCallbacks | None,
) -> tuple[int, JsonObject]:
    if callbacks is None:
        return 404, {"error": "threads_integration_unavailable"}
    try:
        signed_value = _signed_request_form(body)
        if path == DEAUTHORIZE_CALLBACK_PATH:
            _ = callbacks.deauthorize(signed_value, now=now or datetime.now(UTC))
            return 200, {"success": True}
        receipt = callbacks.delete(signed_value, now=now or datetime.now(UTC))
    except ThreadsProviderCallbackError:
        return 403, {"error": "threads_callback_rejected"}
    except (OSError, sqlite3.Error):
        return 503, {"error": "threads_callback_unavailable"}
    return 200, {
        "url": callbacks.status_url(receipt.confirmation_code),
        "confirmation_code": receipt.confirmation_code,
    }


def _dispatch_status(
    path: str, callbacks: ThreadsPrivacyCallbacks | None
) -> tuple[int, JsonObject]:
    if callbacks is None:
        return 404, {"error": "threads_integration_unavailable"}
    confirmation_code = path[len(DATA_DELETION_STATUS_PREFIX) :]
    if not confirmation_code or "/" in confirmation_code:
        return 404, {"error": "threads_deletion_not_found"}
    receipt = callbacks.status(confirmation_code)
    if receipt is None:
        return 404, {"error": "threads_deletion_not_found"}
    return 200, receipt.model_dump(mode="json")


def threads_privacy_route(method: str, path: str) -> bool:
    return (
        method == "POST"
        and path in {DEAUTHORIZE_CALLBACK_PATH, DATA_DELETION_CALLBACK_PATH}
    ) or (
        method == "GET" and path.startswith(DATA_DELETION_STATUS_PREFIX)
    )


def _signed_request_form(body: bytes) -> str:
    if not body or len(body) > _MAX_BODY_BYTES:
        raise ThreadsProviderCallbackError(_INVALID_CALLBACK)
    try:
        form = parse_qs(body.decode(), keep_blank_values=True, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        raise ThreadsProviderCallbackError(_INVALID_CALLBACK) from None
    values = form.get("signed_request", [])
    if set(form) != {"signed_request"} or len(values) != 1 or not values[0]:
        raise ThreadsProviderCallbackError(_INVALID_CALLBACK)
    return values[0]


__all__ = ["dispatch_threads_privacy", "threads_privacy_route"]

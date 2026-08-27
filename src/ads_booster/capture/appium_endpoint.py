from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.contracts import ErrorCode


def validate_appium_server_url(server_url: str) -> str:
    try:
        parsed = urlsplit(server_url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise CaptureAdapterError(
            code=ErrorCode.APPIUM_ENDPOINT_REJECTED,
            message="Appium server URL is malformed",
        ) from error
    address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    if host is not None:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
    trusted = (
        parsed.scheme == "http"
        and address is not None
        and address.is_loopback
        and port is not None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )
    if not trusted:
        raise CaptureAdapterError(
            code=ErrorCode.APPIUM_ENDPOINT_REJECTED,
            message="Appium server must be an explicit plain HTTP loopback endpoint",
        )
    return server_url

import pytest

from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.contracts import ErrorCode


@pytest.mark.parametrize(
    "server_url",
    [
        "http://example.com:4723",
        "https://127.0.0.1:4723",
        "http://127.0.0.1:4723?token=secret",
        "http://user:pass@127.0.0.1:4723",
    ],
)
def test_validate_appium_server_when_endpoint_is_not_plain_loopback(
    server_url: str,
) -> None:
    with pytest.raises(CaptureAdapterError) as raised:
        _ = validate_appium_server_url(server_url)

    assert raised.value.code is ErrorCode.APPIUM_ENDPOINT_REJECTED


def test_validate_appium_server_when_ipv6_loopback_is_used() -> None:
    endpoint = validate_appium_server_url("http://[::1]:4723/wd/hub")

    assert endpoint == "http://[::1]:4723/wd/hub"

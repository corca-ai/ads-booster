from __future__ import annotations

import pytest

from ads_booster.threads.provider_callbacks import (
    ThreadsProviderCallbackError,
    verify_meta_signed_request,
)
from tests.marketing.agent_service.threads_callback_fixtures import (
    FAKE_APP_SECRET,
    signed_request,
)


def test_meta_signed_request_returns_verified_provider_user() -> None:
    # Given a Meta payload signed independently with the configured App Secret.
    request = signed_request("provider-user-1")

    # When the callback boundary verifies and parses it.
    parsed = verify_meta_signed_request(request, FAKE_APP_SECRET)

    # Then only the typed, authenticated provider identity crosses the boundary.
    assert parsed.user_id == "provider-user-1"


@pytest.mark.parametrize(
    "signed_value",
    [
        "malformed",
        signed_request("provider-user-1") + "tampered",
        signed_request("provider-user-1", algorithm="HMAC-SHA512"),
    ],
)
def test_meta_signed_request_rejects_untrusted_payload(signed_value: str) -> None:
    # Given malformed, modified, or unsupported signed callback input.
    # When the provider boundary attempts verification.
    with pytest.raises(ThreadsProviderCallbackError) as raised:
        _ = verify_meta_signed_request(signed_value, FAKE_APP_SECRET)

    # Then callers receive one non-oracular rejection code.
    assert str(raised.value) == "threads_callback_invalid"

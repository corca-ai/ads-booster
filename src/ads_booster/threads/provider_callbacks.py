from __future__ import annotations

import base64
import binascii
import hmac
from hashlib import sha256
from typing import Annotated, Final, Literal

from pydantic import Field, ValidationError

from ads_booster.contracts.models import ContractModel


class MetaSignedRequest(ContractModel):
    algorithm: Literal["HMAC-SHA256"]
    user_id: Annotated[str, Field(min_length=1, max_length=160)]
    issued_at: Annotated[int, Field(ge=0)]
    expires: Annotated[int, Field(ge=0)] | None = None


class ThreadsProviderCallbackError(ValueError):
    """Reject an unauthenticated or malformed Meta provider callback."""


_MAX_SIGNED_REQUEST_BYTES: Final = 8_192
_INVALID_CALLBACK: Final = "threads_callback_invalid"


def verify_meta_signed_request(value: str, app_secret: str) -> MetaSignedRequest:
    encoded_signature, separator, encoded_payload = value.partition(".")
    if (
        not app_secret
        or not separator
        or not encoded_signature
        or not encoded_payload
        or len(value.encode()) > _MAX_SIGNED_REQUEST_BYTES
    ):
        raise ThreadsProviderCallbackError(_INVALID_CALLBACK)
    try:
        signature = base64.b64decode(
            _padded(encoded_signature), altchars=b"-_", validate=True
        )
        payload = base64.b64decode(_padded(encoded_payload), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise ThreadsProviderCallbackError(_INVALID_CALLBACK) from None
    expected = hmac.new(app_secret.encode(), encoded_payload.encode(), sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ThreadsProviderCallbackError(_INVALID_CALLBACK)
    try:
        return MetaSignedRequest.model_validate_json(payload)
    except ValidationError:
        raise ThreadsProviderCallbackError(_INVALID_CALLBACK) from None


def _padded(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode()


__all__ = [
    "MetaSignedRequest",
    "ThreadsProviderCallbackError",
    "verify_meta_signed_request",
]

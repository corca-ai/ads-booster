from __future__ import annotations

import base64
import hmac
import json
from hashlib import sha256

FAKE_APP_SECRET = "fixture-app-secret"  # noqa: S105 - test-only credential.


def signed_request(
    user_id: str,
    *,
    algorithm: str = "HMAC-SHA256",
    issued_at: int = 1_789_416_000,
) -> str:
    payload = json.dumps(
        {"algorithm": algorithm, "issued_at": issued_at, "user_id": user_id},
        separators=(",", ":"),
    ).encode()
    encoded_payload = _base64url(payload)
    signature = hmac.new(FAKE_APP_SECRET.encode(), encoded_payload.encode(), sha256).digest()
    return f"{_base64url(signature)}.{encoded_payload}"


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


__all__ = ["FAKE_APP_SECRET", "signed_request"]

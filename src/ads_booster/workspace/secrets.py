from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Final

_ALGORITHM: Final = "scrypt"
_SALT_BYTES: Final = 16
_CODE_BYTES: Final = 24
_MAX_CODE_LENGTH: Final = 512
_ENCODED_PARTS: Final = 3


def issue_code() -> tuple[str, str]:
    code = secrets.token_urlsafe(_CODE_BYTES)
    return code, hash_code(code)


def hash_code(code: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(code.encode(), salt=salt, n=2**14, r=8, p=1)
    return "$".join(
        (
            _ALGORITHM,
            base64.urlsafe_b64encode(salt).decode(),
            base64.urlsafe_b64encode(digest).decode(),
        )
    )


def verify_code(code: str, encoded: str) -> bool:
    if not code or len(code) > _MAX_CODE_LENGTH:
        return False
    parts = encoded.split("$")
    if len(parts) != _ENCODED_PARTS or parts[0] != _ALGORITHM:
        return False
    try:
        salt = base64.urlsafe_b64decode(parts[1].encode())
        expected = base64.urlsafe_b64decode(parts[2].encode())
    except ValueError, UnicodeError:
        return False
    actual = hashlib.scrypt(code.encode(), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(actual, expected)

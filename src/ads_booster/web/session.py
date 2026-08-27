from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Final, final, override

from pydantic import BaseModel, ConfigDict, ValidationError

from ads_booster.workspace import MemberId, WorkspaceId

type Clock = Callable[[], float]
_SIGNATURE_BYTES: Final = hashlib.sha256().digest_size


class SessionClaims(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    workspace_id: WorkspaceId
    member_id: MemberId
    workspace_code_version: int
    member_code_version: int
    expires_at: float


@final
class InvalidSessionError(ValueError):
    @override
    def __str__(self) -> str:
        return "invalid or expired session"


@dataclass(frozen=True, slots=True)
class SessionCodec:
    secret: bytes
    clock: Clock

    def issue(self, claims: SessionClaims) -> str:
        payload = claims.model_dump_json().encode()
        signature = hmac.digest(self.secret, payload, "sha256")
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def decode(self, token: str) -> SessionClaims:
        try:
            padded = token + "=" * (-len(token) % 4)
            signed = base64.b64decode(padded, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as error:
            raise InvalidSessionError from error
        if len(signed) <= _SIGNATURE_BYTES:
            raise InvalidSessionError
        payload = signed[:-_SIGNATURE_BYTES]
        signature = signed[-_SIGNATURE_BYTES:]
        expected = hmac.digest(self.secret, payload, "sha256")
        if not hmac.compare_digest(signature, expected):
            raise InvalidSessionError
        try:
            claims = SessionClaims.model_validate_json(payload)
        except ValidationError as error:
            raise InvalidSessionError from error
        if claims.expires_at <= self.clock():
            raise InvalidSessionError
        return claims

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from ads_booster.knowledge.deletion import (
    ReplicaDeletionState,
    ReplicaPurgeReceipt,
    ReplicaPurgeRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class ReplicaPurgeHttpResponse(Protocol):
    def read(self) -> bytes: ...

    def __enter__(self) -> ReplicaPurgeHttpResponse: ...

    def __exit__(self, *_: object) -> None: ...


@dataclass(frozen=True, slots=True)
class CloudflareReplicaPurgePort:
    origin: str
    bearer_token: str
    opener: Callable[..., ReplicaPurgeHttpResponse] = urlopen

    def __post_init__(self) -> None:
        parsed = urlsplit(self.origin)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
            raise ValueError("knowledge_replica_purge_origin_invalid")
        if not self.bearer_token:
            raise ValueError("knowledge_replica_purge_token_missing")

    def purge(self, request: ReplicaPurgeRequest) -> ReplicaPurgeReceipt:
        if request.system_id not in {"cloudflare", "mac"}:
            raise ValueError("knowledge_replica_purge_system_invalid")
        target = (
            f"{self.origin.rstrip('/')}/v1/knowledge/context-transfers/"
            f"{quote(request.transfer_id, safe='')}/replicas/"
            f"{quote(request.replica_id, safe='')}"
        )
        method = "DELETE" if request.system_id == "cloudflare" else "GET"
        http_request = Request(
            target,
            method=method,
            headers={
                "authorization": f"Bearer {self.bearer_token}",
                "accept": "application/json",
            },
        )
        checked_at = datetime.now(UTC)
        try:
            with self.opener(http_request, timeout=15) as response:
                payload = json.loads(response.read())
        except (HTTPError, URLError, OSError, UnicodeError, json.JSONDecodeError):
            return _pending_receipt(request, checked_at)
        if not isinstance(payload, dict):
            return _pending_receipt(request, checked_at)
        if (
            payload.get("transfer_id") != request.transfer_id
            or payload.get("replica_id") != request.replica_id
        ):
            return _pending_receipt(request, checked_at)
        state = payload.get("state")
        if state not in {
            ReplicaDeletionState.PURGE_PENDING.value,
            ReplicaDeletionState.PURGED.value,
        }:
            return _pending_receipt(request, checked_at)
        return ReplicaPurgeReceipt(
            receipt_id=_receipt_id(request, str(payload.get("receipt_id", ""))),
            request_id=request.request_id,
            transfer_id=request.transfer_id,
            system_id=request.system_id,
            replica_id=request.replica_id,
            state=state,
            checked_at=checked_at,
        )


def _pending_receipt(
    request: ReplicaPurgeRequest,
    checked_at: datetime,
) -> ReplicaPurgeReceipt:
    return ReplicaPurgeReceipt(
        receipt_id=_receipt_id(request, "pending"),
        request_id=request.request_id,
        transfer_id=request.transfer_id,
        system_id=request.system_id,
        replica_id=request.replica_id,
        state=ReplicaDeletionState.PURGE_PENDING,
        checked_at=checked_at,
    )


def _receipt_id(request: ReplicaPurgeRequest, remote_receipt_id: str) -> str:
    digest = sha256(
        f"{request.model_dump_json()}\x1f{remote_receipt_id}".encode()
    ).hexdigest()[:40]
    return f"replica-purge.{digest}"


__all__ = ["CloudflareReplicaPurgePort"]

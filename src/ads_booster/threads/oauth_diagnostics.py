from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal, TypedDict
from uuid import uuid4

from ads_booster.providers.threads_api import ThreadsApiError

_LOGGER = logging.getLogger(__name__)
type OAuthStage = Literal[
    "fence",
    "authorization_callback",
    "consume_state",
    "exchange_code",
    "exchange_long_lived",
    "user",
    "granted_scopes",
    "validate_scopes",
    "persist_account",
]


class OAuthDiagnosticEvent(TypedDict):
    event: Literal["threads_oauth_completed", "threads_oauth_failed"]
    attempt_id: str
    stage: OAuthStage
    completed_stages: list[OAuthStage]
    elapsed_ms: int
    error_type: str | None
    http_status: int | None
    meta_code: int | None
    meta_subcode: int | None


@dataclass(slots=True)
class OAuthDiagnostics:
    """Accumulate stage metadata only; never retain OAuth inputs or provider messages."""

    attempt_id: str = field(default_factory=lambda: uuid4().hex)
    stage: OAuthStage = "fence"
    completed_stages: list[OAuthStage] = field(default_factory=list)
    started_at: float = field(default_factory=monotonic)

    def advance(self, stage: OAuthStage) -> None:
        self.completed_stages.append(self.stage)
        self.stage = stage

    def finish(self, error: Exception | None = None) -> None:
        provider = error if isinstance(error, ThreadsApiError) else None
        payload: OAuthDiagnosticEvent = {
            "event": "threads_oauth_completed" if error is None else "threads_oauth_failed",
            "attempt_id": self.attempt_id,
            "stage": self.stage,
            "completed_stages": [*self.completed_stages, self.stage]
            if error is None
            else self.completed_stages,
            "elapsed_ms": round((monotonic() - self.started_at) * 1000),
            "error_type": None if error is None else type(error).__name__,
            "http_status": None if provider is None else provider.status,
            "meta_code": None if provider is None else provider.code,
            "meta_subcode": None if provider is None else provider.subcode,
        }
        _LOGGER.log(logging.INFO if error is None else logging.WARNING, json.dumps(payload))

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Annotated, ClassVar, NewType

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from trace_capture.contracts.generation import MarketingContextBundle as _MarketingContextBundle
from trace_capture.workspace import WorkspaceId as _WorkspaceId

if TYPE_CHECKING:
    from trace_capture.contracts.generation import MarketingContextBundle
    from trace_capture.workspace import WorkspaceId

QueueId = NewType("QueueId", str)


@unique
class QueueState(StrEnum):
    SUBMITTED = "submitted"
    CLAIMED = "claimed"
    RUNNING = "running"
    REVIEW = "review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


class QueueModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class QueueSubmission(QueueModel):
    workspace_id: WorkspaceId
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]
    bundle: MarketingContextBundle
    due_at: datetime
    max_attempts: Annotated[int, Field(ge=1, le=20)] = 3

    @model_validator(mode="after")
    def require_utc_due_at(self) -> QueueSubmission:
        if self.due_at.tzinfo is None or self.due_at.utcoffset() != UTC.utcoffset(self.due_at):
            code = "non_utc_due_at"
            message = "queue due_at must be UTC"
            raise PydanticCustomError(code, message)
        return self


class QueueRecord(QueueModel):
    queue_id: QueueId
    workspace_id: WorkspaceId
    idempotency_key: str
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle: MarketingContextBundle
    state: QueueState
    due_at: datetime
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    worker_id: str | None = None
    lease_until: datetime | None = None
    run_id: str | None = None
    run_idempotency_key: str | None = None
    artifact_path: str | None = None
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    failure_code: str | None = None
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class QueueCompletion:
    state: QueueState
    run_id: str | None = None
    run_idempotency_key: str | None = None
    artifact_path: str | None = None
    artifact_sha256: str | None = None
    failure_code: str | None = None


_ = QueueSubmission.model_rebuild(
    _types_namespace={
        "MarketingContextBundle": _MarketingContextBundle,
        "WorkspaceId": _WorkspaceId,
    }
)
_ = QueueRecord.model_rebuild(
    _types_namespace={
        "MarketingContextBundle": _MarketingContextBundle,
        "WorkspaceId": _WorkspaceId,
    }
)

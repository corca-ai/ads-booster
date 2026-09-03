from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.transport.json_types import JsonObject


@unique
class TaskKind(StrEnum):
    RESEARCH = "research"
    GENERATE_CANDIDATES = "generate_candidates"
    MARKETING_JUDGMENT = "marketing_judgment"
    CAPTURE = "capture"
    PUBLISH = "publish"
    SAMPLE_METRICS = "sample_metrics"


@unique
class TaskStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN_SIDE_EFFECT = "unknown_side_effect"


def task_unknown_side_effect_code(kind: TaskKind) -> str:
    if kind is TaskKind.CAPTURE:
        return "native_appium_side_effect_unknown"
    return f"{kind.value}_side_effect_unknown"


@unique
class WorkerTaskEventType(StrEnum):
    PREPARATION_STARTED = "preparation_started"
    PREPARATION_FAILED = "preparation_failed"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_UNKNOWN = "execution_unknown"


@unique
class ApprovalPhase(StrEnum):
    CANDIDATES = "candidates"
    PUBLICATION = "publication"


@unique
class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class MarketingModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class MarketingTask(MarketingModel):
    schema_version: Literal["1"] = "1"
    task_id: Annotated[str, Field(min_length=1, max_length=128)]
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    account_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    kind: TaskKind
    idempotency_key: Annotated[str, Field(min_length=1, max_length=256)]
    payload: JsonObject
    created_at: datetime
    credential_ref: Annotated[str | None, Field(max_length=256)] = None

    @model_validator(mode="after")
    def require_utc_created_at(self) -> MarketingTask:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() != UTC.utcoffset(
            self.created_at
        ):
            raise PydanticCustomError("non_utc_created_at", "task created_at must be UTC")
        return self


class QueueLease(MarketingModel):
    message_id: str
    lease_id: str
    attempts: int = Field(ge=0)
    task: MarketingTask


class ArtifactReference(MarketingModel):
    uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TaskResult(MarketingModel):
    status: TaskStatus
    output: JsonObject = Field(default_factory=dict)
    artifacts: tuple[ArtifactReference, ...] = ()
    failure_code: str | None = None

    @model_validator(mode="after")
    def require_failure_code(self) -> TaskResult:
        if self.status is not TaskStatus.SUCCEEDED and not self.failure_code:
            raise PydanticCustomError(
                "missing_failure_code",
                "non-successful task results require failure_code",
            )
        return self


class TaskCallback(MarketingModel):
    schema_version: Literal["1"] = "1"
    callback_id: str
    task_id: str
    run_id: str
    account_id: str
    kind: TaskKind
    result: TaskResult
    completed_at: datetime


class ReviewApproval(MarketingModel):
    """Durable human-review event emitted by the installed workspace bridge."""

    schema_version: Literal["1"] = "1"
    approval_id: Annotated[str, Field(min_length=1, max_length=320)]
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    account_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    phase: ApprovalPhase
    decision: ApprovalDecision
    candidate_ids: Annotated[tuple[str, ...], Field(max_length=8)] = ()
    reviewed_at: datetime

    @model_validator(mode="after")
    def validate_approval(self) -> ReviewApproval:
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() != UTC.utcoffset(
            self.reviewed_at
        ):
            raise PydanticCustomError("non_utc_reviewed_at", "reviewed_at must be UTC")
        if (
            self.phase is ApprovalPhase.CANDIDATES
            and self.decision is ApprovalDecision.APPROVED
            and not self.candidate_ids
        ):
            raise PydanticCustomError(
                "missing_candidate_ids",
                "approved candidate review requires candidate_ids",
            )
        if self.phase is ApprovalPhase.PUBLICATION and self.candidate_ids:
            raise PydanticCustomError(
                "unexpected_candidate_ids",
                "publication review does not accept candidate_ids",
            )
        return self

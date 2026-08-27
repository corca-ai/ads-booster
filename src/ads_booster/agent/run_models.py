from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar, Final, NewType, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.transport.json_types import JsonObject  # noqa: TC001

AgentRunId = NewType("AgentRunId", str)
ConnectorId = NewType("ConnectorId", str)
ToolName = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_-]*$"),
]
TOOL_POLICY_OVERLAP: Final = "tool_policy_overlap"
TOOL_POLICY_OVERLAP_MESSAGE: Final = "a tool cannot be both allowed and denied"


class AgentRunModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class AgentRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ObservationKind(StrEnum):
    SYSTEM = "system"
    MODEL = "model"
    TOOL = "tool"
    ARTIFACT = "artifact"
    APPROVAL = "approval"
    INPUT = "input"
    FAILURE = "failure"


class CompletionDisposition(StrEnum):
    CONTINUE = "continue"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class AgentGoal(AgentRunModel):
    objective: Annotated[str, Field(min_length=1, max_length=20_000)]
    success_criteria: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    context: JsonObject = Field(default_factory=dict)


class ToolPolicy(AgentRunModel):
    allow: tuple[ToolName, ...]
    deny: tuple[ToolName, ...] = ()

    @model_validator(mode="after")
    def require_disjoint_entries(self) -> Self:
        if set(self.allow) & set(self.deny):
            raise PydanticCustomError(
                TOOL_POLICY_OVERLAP,
                TOOL_POLICY_OVERLAP_MESSAGE,
            )
        return self


class AgentObservation(AgentRunModel):
    sequence: int = Field(ge=1)
    kind: ObservationKind
    summary: Annotated[str, Field(min_length=1, max_length=20_000)]
    data: JsonObject = Field(default_factory=dict)


class AgentRun(AgentRunModel):
    run_id: AgentRunId
    connector_id: ConnectorId
    connector_version: Annotated[str, Field(min_length=1, max_length=80)]
    goal: AgentGoal
    tool_policy: ToolPolicy
    state: AgentRunState = AgentRunState.QUEUED
    revision: int = Field(default=1, ge=1)
    history: tuple[JsonObject, ...] = ()
    observations: tuple[AgentObservation, ...] = ()
    terminal_reason: Annotated[str, Field(min_length=1, max_length=20_000)] | None = None
    created_at: float = Field(default=0, ge=0)
    updated_at: float = Field(default=0, ge=0)


class AgentRunUpdate(AgentRunModel):
    expected_revision: int = Field(ge=1)
    state: AgentRunState
    at: float = Field(ge=0)
    history: tuple[JsonObject, ...] | None = None
    observation: AgentObservation | None = None
    terminal_reason: Annotated[str, Field(min_length=1, max_length=20_000)] | None = None


class AgentReview(AgentRunModel):
    expected_revision: int = Field(ge=1)
    accepted: bool
    note: Annotated[str, Field(min_length=1, max_length=2_000)] | None = None
    at: float = Field(ge=0)


class AgentInput(AgentRunModel):
    expected_revision: int = Field(ge=1)
    text: Annotated[str, Field(min_length=1, max_length=20_000)]
    at: float = Field(ge=0)


class CompletionDecision(AgentRunModel):
    disposition: CompletionDisposition
    message: Annotated[str, Field(min_length=1, max_length=20_000)]
    data: JsonObject = Field(default_factory=dict)

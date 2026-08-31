from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.models import ContractModel, Identifier, Sha256Digest

if TYPE_CHECKING:
    from ads_booster.capture.codex_appium_job import CodexAppiumJobContract

_TIME_PREFIX: Final = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)\s+(.+)$")
_CALENDAR_REQUEST_INVALID: Final = "calendar_automation_request_invalid"
_CALENDAR_REQUEST_INVALID_MESSAGE: Final = "calendar automation request fields disagree"
_CALENDAR_RESULT_INVALID: Final = "calendar_automation_result_invalid"
_CALENDAR_RESULT_INVALID_MESSAGE: Final = "calendar automation result fields disagree"


class CalendarAutomationOperation(StrEnum):
    PREPARE = "prepare"
    CLEANUP = "cleanup"


class CalendarAutomationEvent(ContractModel):
    title: Annotated[str, Field(min_length=1, max_length=80)]
    starts_at_epoch: int = Field(ge=0)
    ends_at_epoch: int = Field(gt=0)
    is_all_day: bool

    @model_validator(mode="after")
    def require_positive_duration(self) -> CalendarAutomationEvent:
        if self.ends_at_epoch <= self.starts_at_epoch:
            raise PydanticCustomError(
                _CALENDAR_REQUEST_INVALID,
                _CALENDAR_REQUEST_INVALID_MESSAGE,
            )
        return self


class CalendarAutomationRequest(ContractModel):
    schema_version: Literal["trace.marketing-calendar-automation.v1"]
    operation: CalendarAutomationOperation
    request_sha256: Sha256Digest
    calendar_namespace: Identifier
    calendar_identifier: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    events: tuple[CalendarAutomationEvent, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def require_operation_fields(self) -> CalendarAutomationRequest:
        valid = (
            self.operation is CalendarAutomationOperation.PREPARE
            and self.calendar_identifier is None
            and bool(self.events)
        ) or (
            self.operation is CalendarAutomationOperation.CLEANUP
            and self.calendar_identifier is not None
            and bool(self.events)
        )
        if not valid:
            raise PydanticCustomError(
                _CALENDAR_REQUEST_INVALID,
                _CALENDAR_REQUEST_INVALID_MESSAGE,
            )
        return self


class CalendarAutomationResult(ContractModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trace.marketing-calendar-automation-result.v1"]
    operation: CalendarAutomationOperation
    request_sha256: Sha256Digest
    calendar_namespace: Identifier
    status: Literal["completed", "failed"]
    calendar_identifier: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    event_count: int = Field(ge=0, le=8)
    error_code: Annotated[str, Field(pattern=r"^[a-z0-9_]+$")] | None = None

    @model_validator(mode="after")
    def require_status_fields(self) -> CalendarAutomationResult:
        if (self.status == "completed") == (self.error_code is not None):
            raise PydanticCustomError(
                _CALENDAR_RESULT_INVALID,
                _CALENDAR_RESULT_INVALID_MESSAGE,
            )
        return self


@dataclass(frozen=True, slots=True)
class CalendarPreparation:
    request_sha256: str
    calendar_namespace: str
    calendar_identifier: str
    event_count: int


def build_calendar_events(
    contract: CodexAppiumJobContract,
) -> tuple[CalendarAutomationEvent, ...]:
    trace_items = contract.context.promotion_material.trace_items or ()
    zone = ZoneInfo(contract.time_zone)
    local_reference = contract.context.reference_date.astimezone(zone)
    start_of_day = datetime.combine(local_reference.date(), time(), tzinfo=zone)
    events: list[CalendarAutomationEvent] = []
    for item in trace_items:
        matched = _TIME_PREFIX.fullmatch(item)
        if matched is None:
            start = start_of_day
            end = start + timedelta(days=1)
            title = item
            is_all_day = True
        else:
            start = datetime.combine(
                local_reference.date(),
                time(hour=int(matched.group(1)), minute=int(matched.group(2))),
                tzinfo=zone,
            )
            end = start + timedelta(hours=1)
            title = matched.group(3)
            is_all_day = False
        events.append(
            CalendarAutomationEvent(
                title=title,
                starts_at_epoch=int(start.timestamp()),
                ends_at_epoch=int(end.timestamp()),
                is_all_day=is_all_day,
            )
        )
    return tuple(events)


__all__ = [
    "CalendarAutomationEvent",
    "CalendarAutomationOperation",
    "CalendarAutomationRequest",
    "CalendarAutomationResult",
    "CalendarPreparation",
    "build_calendar_events",
]

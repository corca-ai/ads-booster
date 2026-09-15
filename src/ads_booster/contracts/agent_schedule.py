from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from enum import StrEnum, unique
from typing import Annotated, Literal, LiteralString, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, BoundedId
from ads_booster.contracts.models import ContractModel, Sha256Digest


@unique
class ScheduleStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"


@unique
class ScheduleApprovalMode(StrEnum):
    REVIEW_EACH = "review_each"
    AUTO = "auto"


@unique
class ScheduleMisfirePolicy(StrEnum):
    SKIP = "skip"
    COALESCE = "coalesce"
    AWAIT_INPUT = "await_input"


@unique
class ScheduleOccurrenceState(StrEnum):
    RESERVED = "reserved"
    ADMITTED = "admitted"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED_OVERLAP = "skipped_overlap"
    SKIPPED_MISFIRE = "skipped_misfire"


class OnceScheduleRule(ContractModel):
    kind: Literal["once"] = "once"
    at: datetime

    @model_validator(mode="after")
    def require_utc(self) -> Self:
        _require_utc(self.at, "schedule_once_time_requires_utc")
        return self


class IntervalScheduleRule(ContractModel):
    kind: Literal["interval"] = "interval"
    anchor: datetime
    every_minutes: Annotated[int, Field(ge=1, le=525_600)]

    @model_validator(mode="after")
    def require_utc(self) -> Self:
        _require_utc(self.anchor, "schedule_interval_anchor_requires_utc")
        return self


class DailyScheduleRule(ContractModel):
    kind: Literal["daily"] = "daily"
    hour: Annotated[int, Field(ge=0, le=23)]
    minute: Annotated[int, Field(ge=0, le=59)]


class WeeklyScheduleRule(ContractModel):
    kind: Literal["weekly"] = "weekly"
    weekdays: Annotated[tuple[int, ...], Field(min_length=1, max_length=7)]
    hour: Annotated[int, Field(ge=0, le=23)]
    minute: Annotated[int, Field(ge=0, le=59)]

    @model_validator(mode="after")
    def require_unique_weekdays(self) -> Self:
        if any(day < 0 or day > 6 for day in self.weekdays):
            raise PydanticCustomError("schedule_weekday_invalid", "weekdays must be between 0 and 6")
        if len(set(self.weekdays)) != len(self.weekdays):
            raise PydanticCustomError("schedule_weekday_duplicate", "weekdays must be unique")
        return self


class MonthlyScheduleRule(ContractModel):
    kind: Literal["monthly"] = "monthly"
    month_days: Annotated[tuple[int, ...], Field(min_length=1, max_length=31)]
    hour: Annotated[int, Field(ge=0, le=23)]
    minute: Annotated[int, Field(ge=0, le=59)]

    @model_validator(mode="after")
    def require_unique_month_days(self) -> Self:
        if any(day < 1 or day > 31 for day in self.month_days):
            raise PydanticCustomError(
                "schedule_month_day_invalid", "month days must be between 1 and 31"
            )
        if len(set(self.month_days)) != len(self.month_days):
            raise PydanticCustomError("schedule_month_day_duplicate", "month days must be unique")
        return self


type ScheduleRule = Annotated[
    OnceScheduleRule
    | IntervalScheduleRule
    | DailyScheduleRule
    | WeeklyScheduleRule
    | MonthlyScheduleRule,
    Field(discriminator="kind"),
]


class ScheduleAuthority(ContractModel):
    workspace_id: BoundedId
    member_id: BoundedId
    external_user_id: BoundedId
    conversation_id: BoundedId
    source_event_id: BoundedId
    source_sha256: Sha256Digest


class ScheduleDestination(ContractModel):
    kind: Literal["slack"] = "slack"
    channel_id: BoundedId
    thread_ts: Annotated[str, Field(min_length=1, max_length=80)] | None = None


class AgentSchedule(ContractModel):
    schema_version: Literal["trace.agent-schedule.v1"] = "trace.agent-schedule.v1"
    schedule_id: BoundedId
    tenant_id: BoundedId
    authority: ScheduleAuthority
    destination: ScheduleDestination
    goal: AgentGoal
    budget: AgentBudget
    rule: ScheduleRule
    timezone: Annotated[str, Field(min_length=1, max_length=80)]
    approval_mode: ScheduleApprovalMode = ScheduleApprovalMode.REVIEW_EACH
    misfire_policy: ScheduleMisfirePolicy = ScheduleMisfirePolicy.SKIP
    allowed_effects: Annotated[tuple[BoundedId, ...], Field(max_length=32)] = ()
    allowed_resource_ids: Annotated[tuple[BoundedId, ...], Field(max_length=128)] = ()
    max_occurrences: Annotated[int, Field(ge=1, le=100_000)] | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    revision: Annotated[int, Field(ge=1)] = 1
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def require_valid_lifetime(self) -> Self:
        for value in (self.starts_at, self.created_at, self.updated_at):
            _require_utc(value, "schedule_time_requires_utc")
        if self.ends_at is not None:
            _require_utc(self.ends_at, "schedule_time_requires_utc")
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise PydanticCustomError("schedule_lifetime_invalid", "ends_at must follow starts_at")
        if self.updated_at < self.created_at:
            raise PydanticCustomError(
                "schedule_update_time_invalid", "updated_at must not precede created_at"
            )
        try:
            _ = ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise PydanticCustomError("schedule_timezone_invalid", "unknown IANA timezone") from error
        if len(set(self.allowed_effects)) != len(self.allowed_effects):
            raise PydanticCustomError(
                "schedule_effect_duplicate", "allowed effects must be unique"
            )
        if len(set(self.allowed_resource_ids)) != len(self.allowed_resource_ids):
            raise PydanticCustomError(
                "schedule_resource_duplicate", "allowed resources must be unique"
            )
        if self.approval_mode is ScheduleApprovalMode.AUTO and not self.allowed_effects:
            raise PydanticCustomError(
                "schedule_auto_effect_scope_required",
                "automatic schedules require an exact effect allowlist",
            )
        match self.rule:
            case OnceScheduleRule(at=at) if at < self.starts_at or (
                self.ends_at is not None and at > self.ends_at
            ):
                raise PydanticCustomError(
                    "schedule_once_outside_lifetime",
                    "one-time occurrence must be inside the schedule lifetime",
                )
            case _:
                pass
        return self


class ScheduleOccurrence(ContractModel):
    schema_version: Literal["trace.schedule-occurrence.v1"] = "trace.schedule-occurrence.v1"
    occurrence_id: BoundedId
    schedule_id: BoundedId
    schedule_revision: Annotated[int, Field(ge=1)]
    scheduled_for: datetime
    state: ScheduleOccurrenceState = ScheduleOccurrenceState.RESERVED
    run_id: BoundedId
    created_at: datetime
    updated_at: datetime
    reason: Annotated[str, Field(min_length=1, max_length=1000)] | None = None

    @model_validator(mode="after")
    def require_utc_times(self) -> Self:
        for value in (self.scheduled_for, self.created_at, self.updated_at):
            _require_utc(value, "schedule_occurrence_time_requires_utc")
        if self.updated_at < self.created_at:
            raise PydanticCustomError(
                "schedule_occurrence_update_time_invalid",
                "updated_at must not precede created_at",
            )
        return self


def next_occurrence(schedule: AgentSchedule, *, after: datetime) -> datetime | None:
    _require_utc(after, "schedule_cursor_requires_utc")
    if schedule.status is not ScheduleStatus.ACTIVE:
        return None
    cursor = max(after, schedule.starts_at - timedelta(microseconds=1))
    match schedule.rule:
        case OnceScheduleRule(at=at):
            candidate = at if at > cursor else None
        case IntervalScheduleRule(anchor=anchor, every_minutes=every_minutes):
            interval = timedelta(minutes=every_minutes)
            if cursor < anchor:
                candidate = anchor
            else:
                elapsed = cursor - anchor
                candidate = anchor + interval * (elapsed // interval + 1)
        case DailyScheduleRule(hour=hour, minute=minute):
            candidate = _next_calendar(schedule.timezone, cursor, hour, minute, None, None)
        case WeeklyScheduleRule(weekdays=weekdays, hour=hour, minute=minute):
            candidate = _next_calendar(
                schedule.timezone, cursor, hour, minute, frozenset(weekdays), None
            )
        case MonthlyScheduleRule(month_days=month_days, hour=hour, minute=minute):
            candidate = _next_calendar(
                schedule.timezone, cursor, hour, minute, None, frozenset(month_days)
            )
    if candidate is None or candidate < schedule.starts_at:
        return None
    if schedule.ends_at is not None and candidate > schedule.ends_at:
        return None
    return candidate


def _next_calendar(
    timezone: str,
    after: datetime,
    hour: int,
    minute: int,
    weekdays: frozenset[int] | None,
    month_days: frozenset[int] | None,
) -> datetime | None:
    zone = ZoneInfo(timezone)
    local_date = after.astimezone(zone).date()
    for offset in range(367):
        day = local_date + timedelta(days=offset)
        if weekdays is not None and day.weekday() not in weekdays:
            continue
        if month_days is not None and day.day not in month_days:
            continue
        wall = datetime.combine(day, time(hour, minute), tzinfo=zone).replace(fold=0)
        candidate = wall.astimezone(UTC)
        if candidate <= after:
            continue
        if candidate.astimezone(zone).replace(tzinfo=None) != wall.replace(tzinfo=None):
            continue
        return candidate
    return None


def _require_utc(value: datetime, code: LiteralString) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise PydanticCustomError(code, "datetime must be UTC")


__all__ = [
    "AgentSchedule",
    "DailyScheduleRule",
    "IntervalScheduleRule",
    "MonthlyScheduleRule",
    "OnceScheduleRule",
    "ScheduleApprovalMode",
    "ScheduleAuthority",
    "ScheduleDestination",
    "ScheduleMisfirePolicy",
    "ScheduleOccurrence",
    "ScheduleOccurrenceState",
    "ScheduleRule",
    "ScheduleStatus",
    "WeeklyScheduleRule",
    "next_occurrence",
]

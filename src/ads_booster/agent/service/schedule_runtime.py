from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol

from pydantic import TypeAdapter

from ads_booster.agent.service.application import CreateAgentRunRequest, MarketingAgentService
from ads_booster.agent.service.drive_work import DriveOrigin, DriveWorkQueue
from ads_booster.agent.service.schedule_repository import ScheduleRepository
from ads_booster.contracts.agent_run import (
    AgentGoal,
    AgentRecordKind,
    AgentRun,
    AgentRunState,
    CapabilitySnapshot,
    contract_sha256,
)
from ads_booster.contracts.agent_schedule import (
    AgentSchedule,
    ScheduleApprovalMode,
    ScheduleMisfirePolicy,
    ScheduleOccurrence,
    ScheduleOccurrenceState,
    ScheduleStatus,
    next_occurrence,
)
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ScheduleAuthorityVerifier(Protocol):
    def unavailable_reason(self, schedule: AgentSchedule, *, now: datetime) -> str | None: ...

    def effect_unavailable_reason(
        self,
        schedule: AgentSchedule,
        capability_id: str,
        invocation_input: JsonObject,
        *,
        now: datetime,
    ) -> str | None: ...


class ScheduleNotifier(Protocol):
    def notify(
        self,
        schedule: AgentSchedule,
        occurrence: ScheduleOccurrence,
        run: AgentRun,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ScheduleTickResult:
    admitted: tuple[str, ...]
    skipped: tuple[str, ...]
    blocked: tuple[str, ...]


@dataclass(slots=True)
class ScheduleRuntime:
    repository: ScheduleRepository
    service: MarketingAgentService
    drive_queue: DriveWorkQueue
    authority: ScheduleAuthorityVerifier
    notifier: ScheduleNotifier
    misfire_grace: timedelta = timedelta(minutes=5)
    last_tick_at: datetime | None = field(default=None, init=False)
    last_tick_result: ScheduleTickResult | None = field(default=None, init=False)

    def tick(self, *, now: datetime, limit: int = 200) -> ScheduleTickResult:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("schedule_tick_requires_utc")
        admitted: list[str] = []
        skipped: list[str] = []
        blocked: list[str] = []
        reserved = self.repository.reserved_occurrences(limit=limit)
        for occurrence in reserved:
            schedule = self.repository.get_for_occurrence(occurrence)
            result = self._admit(schedule, occurrence, now=now)
            self._record(result, admitted, skipped, blocked)
        remaining = max(0, limit - len(reserved))
        for schedule in self.repository.list_active(limit=max(1, remaining)):
            if remaining == 0:
                break
            occurrence = self._reserve_due(schedule, now=now)
            if occurrence is None:
                continue
            remaining -= 1
            if occurrence.state is not ScheduleOccurrenceState.RESERVED:
                skipped.append(occurrence.occurrence_id)
                continue
            result = self._admit(schedule, occurrence, now=now)
            self._record(result, admitted, skipped, blocked)
        result = ScheduleTickResult(tuple(admitted), tuple(skipped), tuple(blocked))
        self.last_tick_at = now
        self.last_tick_result = result
        return result

    def health(self) -> JsonObject:
        result = self.last_tick_result
        return _JSON_OBJECT.validate_python({
            "scheduler": "running" if self.last_tick_at is not None else "starting",
            "scheduler_last_tick_at": None
            if self.last_tick_at is None
            else self.last_tick_at.isoformat(),
            "scheduler_last_admitted": 0 if result is None else len(result.admitted),
            "scheduler_last_skipped": 0 if result is None else len(result.skipped),
            "scheduler_last_blocked": 0 if result is None else len(result.blocked),
            "scheduler_backlog": self.repository.health_counts(),
        })

    def work_once(self, *, now: datetime) -> bool:
        claim = self.drive_queue.claim("schedule", now)
        if claim is None:
            return False
        occurrence = self.repository.occurrence_by_run(claim.origin.run_id)
        if occurrence is None:
            self.drive_queue.block(claim, "schedule_occurrence_missing", now)
            return True
        schedule = self.repository.get_for_occurrence(occurrence)
        unavailable = self.authority.unavailable_reason(schedule, now=now)
        if unavailable is not None:
            self.drive_queue.block(claim, unavailable, now)
            current = self.repository.occurrence(occurrence.occurrence_id)
            if current is not None and current.state not in _TERMINAL_OCCURRENCE_STATES:
                _ = self.repository.transition(
                    current.model_copy(
                        update={
                            "state": ScheduleOccurrenceState.BLOCKED,
                            "reason": unavailable,
                            "updated_at": now,
                        }
                    ),
                    expected_state=current.state,
                )
            return True
        notified = False
        with self.drive_queue.ownership(claim=claim, now=now):
            run = self.service.repository.get(claim.origin.tenant_id, claim.origin.run_id)
            if run is None:
                self.drive_queue.block(claim, "schedule_run_missing", now)
                return True
            if claim.phase == "notify":
                notified = self.notifier.notify(schedule, occurrence, run)
            else:
                run = self.service.drive(run.tenant_id, run.run_id, now=now)
                run = self._approve_delegated_effects(schedule, run, now=now)
                target = _occurrence_state(run)
                current = self.repository.occurrence(occurrence.occurrence_id)
                if current is not None and current.state is not target:
                    _ = self.repository.transition(
                        current.model_copy(update={"state": target, "updated_at": now}),
                        expected_state=current.state,
                    )
        if notified:
            self.drive_queue.notification_persisted(schedule.tenant_id, occurrence.occurrence_id)
        return True

    def _approve_delegated_effects(
        self, schedule: AgentSchedule, run: AgentRun, *, now: datetime
    ) -> AgentRun:
        current = run
        if schedule.approval_mode is not ScheduleApprovalMode.AUTO:
            return current
        for _ in range(3):
            if current.state is not AgentRunState.AWAITING_APPROVAL:
                return current
            if self.authority.unavailable_reason(schedule, now=now) is not None:
                return current
            pending = self.service.pending_approval(current.tenant_id, current.run_id)
            if pending is None:
                return current
            descriptor = next(
                (
                    descriptor
                    for record in self.service.repository.records(
                        current.tenant_id, current.run_id
                    )
                    if record.kind is AgentRecordKind.CAPABILITY_SNAPSHOT
                    and record.payload_sha256 == pending.capability_snapshot_sha256
                    for descriptor in CapabilitySnapshot.model_validate(
                        record.payload
                    ).descriptors
                    if contract_sha256(descriptor) == pending.descriptor_sha256
                ),
                None,
            )
            if (
                descriptor is None
                or descriptor.capability_id not in schedule.allowed_effects
                or descriptor.approval_policy.mode != "required"
            ):
                return current
            if (
                self.authority.effect_unavailable_reason(
                    schedule,
                    descriptor.capability_id,
                    pending.input,
                    now=now,
                )
                is not None
            ):
                return current
            current = self.service.decide_approval(
                current.tenant_id,
                current.run_id,
                approver_id=schedule.authority.member_id,
                granted=True,
                expires_at=now + timedelta(minutes=5),
                expected_invocation_sha256=contract_sha256(pending),
                request_event_id=schedule.authority.source_event_id,
                request_text_sha256=schedule.authority.source_sha256,
                now=now,
            )
        return current

    def _reserve_due(
        self, schedule: AgentSchedule, *, now: datetime
    ) -> ScheduleOccurrence | None:
        if (
            schedule.max_occurrences is not None
            and self.repository.occurrence_count(schedule.schedule_id)
            >= schedule.max_occurrences
        ):
            return None
        latest = self.repository.latest_occurrence(schedule.schedule_id)
        cursor = schedule.starts_at - timedelta(microseconds=1)
        if latest is not None:
            cursor = latest.scheduled_for
        due = next_occurrence(schedule, after=cursor)
        if due is None or due > now:
            return None
        if (
            now - due > self.misfire_grace
            and schedule.misfire_policy
            in {ScheduleMisfirePolicy.SKIP, ScheduleMisfirePolicy.COALESCE}
        ):
            due = _coalesced_due(schedule, first_due=due, now=now)
        occurrence_id, run_id = _occurrence_ids(schedule.schedule_id, due)
        state = ScheduleOccurrenceState.RESERVED
        reason = None
        active = self.repository.active_occurrences(schedule.schedule_id)
        if active:
            state = ScheduleOccurrenceState.SKIPPED_OVERLAP
            reason = "previous_occurrence_active"
        elif now - due > self.misfire_grace:
            match schedule.misfire_policy:
                case ScheduleMisfirePolicy.SKIP:
                    state = ScheduleOccurrenceState.SKIPPED_MISFIRE
                    reason = "schedule_misfire_skipped"
                case ScheduleMisfirePolicy.COALESCE:
                    pass
                case ScheduleMisfirePolicy.AWAIT_INPUT:
                    state = ScheduleOccurrenceState.AWAITING_INPUT
                    reason = "schedule_misfire_requires_input"
        occurrence = ScheduleOccurrence(
            occurrence_id=occurrence_id,
            schedule_id=schedule.schedule_id,
            schedule_revision=schedule.revision,
            scheduled_for=due,
            state=state,
            run_id=run_id,
            created_at=now,
            updated_at=now,
            reason=reason,
        )
        return self.repository.reserve(occurrence)

    def _admit(
        self,
        schedule: AgentSchedule,
        occurrence: ScheduleOccurrence,
        *,
        now: datetime,
    ) -> ScheduleOccurrence:
        current_schedule = self.repository.get(schedule.tenant_id, schedule.schedule_id)
        if (
            current_schedule is None
            or current_schedule.revision != occurrence.schedule_revision
            or current_schedule.status is not ScheduleStatus.ACTIVE
        ):
            return self.repository.transition(
                occurrence.model_copy(
                    update={
                        "state": ScheduleOccurrenceState.BLOCKED,
                        "reason": "schedule_revision_inactive",
                        "updated_at": now,
                    }
                ),
                expected_state=ScheduleOccurrenceState.RESERVED,
            )
        unavailable = self.authority.unavailable_reason(schedule, now=now)
        if unavailable is not None:
            return self.repository.transition(
                occurrence.model_copy(
                    update={
                        "state": ScheduleOccurrenceState.BLOCKED,
                        "reason": unavailable,
                        "updated_at": now,
                    }
                ),
                expected_state=ScheduleOccurrenceState.RESERVED,
            )
        origin = DriveOrigin(
            tenant_id=schedule.tenant_id,
            run_id=occurrence.run_id,
            channel="schedule",
            principal_id=schedule.authority.member_id,
            event_id=occurrence.occurrence_id,
            conversation_id=schedule.authority.conversation_id,
        )
        goal = _scheduled_goal(schedule, occurrence)
        schedule_admission = self.repository.admission_for(
            occurrence.occurrence_id, updated_at=now
        )
        with self.drive_queue.ownership(origin=origin, now=now):
            run = self.service.create(
                CreateAgentRunRequest(
                    run_id=occurrence.run_id,
                    tenant_id=schedule.tenant_id,
                    goal=goal,
                    budget=schedule.budget,
                ),
                now=now,
                admission=self.drive_queue.admission(origin, schedule_admission),
            )
        current = self.repository.occurrence(occurrence.occurrence_id)
        if current is None:
            raise ValueError("schedule_occurrence_missing_after_admission")
        target = _occurrence_state(run)
        if target is ScheduleOccurrenceState.ADMITTED:
            return current
        return self.repository.transition(
            current.model_copy(update={"state": target, "updated_at": now}),
            expected_state=ScheduleOccurrenceState.ADMITTED,
        )

    @staticmethod
    def _record(
        occurrence: ScheduleOccurrence,
        admitted: list[str],
        skipped: list[str],
        blocked: list[str],
    ) -> None:
        match occurrence.state:
            case ScheduleOccurrenceState.BLOCKED | ScheduleOccurrenceState.FAILED:
                blocked.append(occurrence.occurrence_id)
            case ScheduleOccurrenceState.SKIPPED_OVERLAP | ScheduleOccurrenceState.SKIPPED_MISFIRE:
                skipped.append(occurrence.occurrence_id)
            case _:
                admitted.append(occurrence.occurrence_id)


def _scheduled_goal(schedule: AgentSchedule, occurrence: ScheduleOccurrence) -> AgentGoal:
    context: JsonObject = {
        **schedule.goal.context,
        "schedule": {
            "schedule_id": schedule.schedule_id,
            "schedule_revision": schedule.revision,
            "occurrence_id": occurrence.occurrence_id,
            "scheduled_for": occurrence.scheduled_for.isoformat(),
            "approval_mode": schedule.approval_mode,
            "allowed_effects": list(schedule.allowed_effects),
            "allowed_resource_ids": list(schedule.allowed_resource_ids),
            "source_event_id": schedule.authority.source_event_id,
            "source_sha256": schedule.authority.source_sha256,
        },
    }
    return schedule.goal.model_copy(update={"context": context})


_TERMINAL_OCCURRENCE_STATES = frozenset(
    {
        ScheduleOccurrenceState.SUCCEEDED,
        ScheduleOccurrenceState.BLOCKED,
        ScheduleOccurrenceState.FAILED,
        ScheduleOccurrenceState.SKIPPED_OVERLAP,
        ScheduleOccurrenceState.SKIPPED_MISFIRE,
    }
)


def _occurrence_state(run: AgentRun) -> ScheduleOccurrenceState:
    match run.state:
        case AgentRunState.COMPLETED:
            return ScheduleOccurrenceState.SUCCEEDED
        case AgentRunState.BLOCKED:
            return ScheduleOccurrenceState.BLOCKED
        case AgentRunState.FAILED:
            return ScheduleOccurrenceState.FAILED
        case AgentRunState.AWAITING_INPUT | AgentRunState.AWAITING_APPROVAL:
            return ScheduleOccurrenceState.AWAITING_INPUT
        case AgentRunState.RUNNING | AgentRunState.AWAITING_TOOL | AgentRunState.AWAITING_RECONCILIATION:
            return ScheduleOccurrenceState.RUNNING
        case AgentRunState.CREATED | AgentRunState.STOPPED:
            return ScheduleOccurrenceState.ADMITTED


def _occurrence_ids(schedule_id: str, scheduled_for: datetime) -> tuple[str, str]:
    digest = sha256(f"{schedule_id}\n{scheduled_for.isoformat()}".encode()).hexdigest()[:24]
    return f"occ-{digest}", f"scheduled-{digest}"


def _coalesced_due(
    schedule: AgentSchedule, *, first_due: datetime, now: datetime
) -> datetime:
    latest = first_due
    for _ in range(10_000):
        following = next_occurrence(schedule, after=latest)
        if following is None or following > now:
            return latest
        latest = following
    raise ValueError("schedule_misfire_window_too_large")


__all__ = [
    "ScheduleAuthorityVerifier",
    "ScheduleNotifier",
    "ScheduleRuntime",
    "ScheduleTickResult",
]

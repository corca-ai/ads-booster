"""Durable daily skill scheduling owned by the canonical Agent Service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ads_booster.contracts.agent_run import (
    AgentBudget,
    AgentRecordKind,
    AgentRun,
    AgentRunState,
    ToolInvocation,
)
from ads_booster.marketing.agent_service.application import (
    CreateAgentRunRequest,
    MarketingAgentService,
)
from ads_booster.marketing.agent_service.skills import MarketingSkillCatalog
from ads_booster.transport.json_types import JsonObject

_SCHEDULE_PREAUTHORIZED_CAPABILITIES = frozenset({"deliver.slack", "store.notion.daily"})
_LAST_HOUR = 23
_LAST_MINUTE = 59


@dataclass(frozen=True, slots=True)
class DailySkillSchedule:
    skill_id: str
    tenant_id: str
    principal_id: str
    timezone: str
    hour: int
    minute: int
    context: JsonObject
    budget: AgentBudget = field(
        default_factory=lambda: AgentBudget(max_tool_calls=6, max_cost_units=100)
    )

    def __post_init__(self) -> None:
        """Reject invalid wall-clock schedules before the service starts."""
        if not 0 <= self.hour <= _LAST_HOUR or not 0 <= self.minute <= _LAST_MINUTE:
            raise ValueError("agent_schedule_time_invalid")
        _ = ZoneInfo(self.timezone)


@dataclass(slots=True)
class AgentSkillScheduler:
    service: MarketingAgentService
    schedules: tuple[DailySkillSchedule, ...]

    def tick(self, *, now: datetime) -> tuple[str, ...]:
        if now.tzinfo is None:
            raise ValueError("agent_schedule_time_must_be_aware")
        started: list[str] = []
        for schedule in self.schedules:
            local = now.astimezone(ZoneInfo(schedule.timezone))
            due = local.replace(hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0)
            if local < due:
                continue
            skill_slug = schedule.skill_id.replace(".", "-").replace("_", "-")
            run_id = f"scheduled-{skill_slug}-{local.date().isoformat()}"
            skill = MarketingSkillCatalog(self.service.registry).require_ready(
                schedule.skill_id, now=now.astimezone(UTC)
            )
            run = self.service.create(
                CreateAgentRunRequest(
                    run_id=run_id,
                    tenant_id=schedule.tenant_id,
                    goal=skill.goal(schedule.context),
                    budget=schedule.budget,
                ),
                now=now.astimezone(UTC),
            )
            run = self._approve_scheduled_deliveries(schedule, run_id, run, now=now)
            started.append(run.run_id)
        return tuple(started)

    def _approve_scheduled_deliveries(
        self,
        schedule: DailySkillSchedule,
        run_id: str,
        run: AgentRun,
        *,
        now: datetime,
    ) -> AgentRun:
        current = run
        for _ in range(3):
            if current.state is not AgentRunState.AWAITING_APPROVAL:
                return current
            invocation = self._latest_invocation(schedule.tenant_id, run_id)
            if invocation is None:
                return current
            descriptor = next(
                (
                    item
                    for item in self.service.registry.current_descriptors(now=now.astimezone(UTC))
                    if item.capability_id in _SCHEDULE_PREAUTHORIZED_CAPABILITIES
                    and item.capability_id == self._capability_for(invocation, schedule.tenant_id)
                ),
                None,
            )
            if descriptor is None:
                return current
            current = self.service.decide_approval(
                schedule.tenant_id,
                run_id,
                approver_id=schedule.principal_id,
                granted=True,
                expires_at=now.astimezone(UTC) + timedelta(minutes=5),
                now=now.astimezone(UTC),
            )
        return current

    def _latest_invocation(self, tenant_id: str, run_id: str) -> ToolInvocation | None:
        for record in reversed(self.service.repository.records(tenant_id, run_id)):
            if record.kind is AgentRecordKind.INVOCATION:
                return ToolInvocation.model_validate(record.payload)
        return None

    def _capability_for(self, invocation: ToolInvocation, tenant_id: str) -> str | None:
        for record in reversed(self.service.repository.records(tenant_id, invocation.run_id)):
            if record.kind is AgentRecordKind.INTENT:
                capability = record.payload.get("capability_id")
                return capability if isinstance(capability, str) else None
        return None


__all__ = ["AgentSkillScheduler", "DailySkillSchedule"]

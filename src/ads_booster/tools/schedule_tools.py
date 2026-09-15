from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter

from ads_booster.agent.service.schedule_repository import ScheduleRepository
from ads_booster.agent.core.registry import ToolRegistry
from ads_booster.contracts.agent_run import AgentBudget, AgentGoal, ToolInvocation, contract_sha256
from ads_booster.contracts.agent_schedule import (
    AgentSchedule,
    ScheduleApprovalMode,
    ScheduleAuthority,
    ScheduleDestination,
    ScheduleMisfirePolicy,
    ScheduleOccurrence,
    ScheduleRule,
    ScheduleStatus,
    next_occurrence,
)
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.tool_capability import EffectClass, ToolCost, ToolDescriptor
from ads_booster.tools.compatibility import DelegatedToolResult
from ads_booster.tools.descriptors import github_issue_descriptor
from ads_booster.transport.json_types import JsonObject

CAPABILITY = "schedule.manage"


@dataclass(frozen=True, slots=True)
class ScheduleActor:
    workspace_id: str
    member_id: str
    external_user_id: str
    conversation_id: str
    source_event_id: str
    source_sha256: str
    channel_id: str
    thread_ts: str | None


class ScheduleActorResolver(Protocol):
    def __call__(self, tenant_id: str, run_id: str) -> ScheduleActor: ...


class CreateScheduleCommand(ContractModel):
    action: Literal["create"]
    goal: AgentGoal
    rule: ScheduleRule
    timezone: Annotated[str, Field(min_length=1, max_length=80)]
    starts_at: datetime
    ends_at: datetime | None = None
    approval_mode: ScheduleApprovalMode = ScheduleApprovalMode.REVIEW_EACH
    misfire_policy: ScheduleMisfirePolicy = ScheduleMisfirePolicy.SKIP
    allowed_effects: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    allowed_resource_ids: Annotated[tuple[str, ...], Field(max_length=128)] = ()
    max_occurrences: Annotated[int, Field(ge=1, le=100_000)] | None = None
    budget: AgentBudget = Field(
        default_factory=lambda: AgentBudget(max_tool_calls=32, max_cost_units=200)
    )


class ReadScheduleCommand(ContractModel):
    action: Literal["list", "get", "occurrences"]
    schedule_id: str | None = None
    limit: Annotated[int, Field(ge=1, le=1000)] = 100


class UpdateScheduleCommand(ContractModel):
    action: Literal["update"]
    schedule_id: str
    expected_revision: Annotated[int, Field(ge=1)]
    rule: ScheduleRule | None = None
    timezone: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    goal: AgentGoal | None = None
    approval_mode: ScheduleApprovalMode | None = None
    misfire_policy: ScheduleMisfirePolicy | None = None
    allowed_effects: Annotated[tuple[str, ...], Field(max_length=32)] | None = None
    allowed_resource_ids: Annotated[tuple[str, ...], Field(max_length=128)] | None = None
    budget: AgentBudget | None = None
    max_occurrences: Annotated[int, Field(ge=1, le=100_000)] | None = None
    clear_ends_at: bool = False
    clear_max_occurrences: bool = False
    move_destination_to_current_conversation: bool = False


class ChangeScheduleStateCommand(ContractModel):
    action: Literal["pause", "resume", "cancel"]
    schedule_id: str
    expected_revision: Annotated[int, Field(ge=1)]


type ScheduleCommand = Annotated[
    CreateScheduleCommand
    | ReadScheduleCommand
    | UpdateScheduleCommand
    | ChangeScheduleStateCommand,
    Field(discriminator="action"),
]

_COMMAND: TypeAdapter[ScheduleCommand] = TypeAdapter(ScheduleCommand)
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class ScheduleManagementTool:
    repository: ScheduleRepository
    actors: ScheduleActorResolver
    registry: ToolRegistry

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        try:
            return self._execute(invocation, descriptor)
        except ValueError as error:
            code = str(error)
            if not code.startswith("schedule_") or "descriptor_mismatch" in code:
                raise
            return DelegatedToolResult(
                disposition="failed", actual_cost_units=0, output={"error": code[:160]}
            )

    def _execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        if descriptor.capability_id != CAPABILITY or invocation.tenant_id is None:
            raise ValueError("schedule_descriptor_mismatch")
        command = _COMMAND.validate_python(invocation.input)
        actor = self.actors(invocation.tenant_id, invocation.run_id)
        match command:
            case CreateScheduleCommand():
                return self._create(command, invocation, actor)
            case ReadScheduleCommand(action=action, schedule_id=schedule_id, limit=limit):
                match action:
                    case "list":
                        schedules = self.repository.list_for_tenant(
                            actor.workspace_id, owner_id=actor.member_id
                        )
                        return self._result(schedules, datetime.now(UTC))
                    case "get":
                        if schedule_id is None:
                            raise ValueError("schedule_id_required")
                        schedule = self.repository.get(actor.workspace_id, schedule_id)
                        if schedule is None or schedule.authority.member_id != actor.member_id:
                            raise ValueError("schedule_not_found")
                        return self._result((schedule,), datetime.now(UTC))
                    case "occurrences":
                        if schedule_id is None:
                            raise ValueError("schedule_id_required")
                        schedule = self.repository.get(actor.workspace_id, schedule_id)
                        if schedule is None or schedule.authority.member_id != actor.member_id:
                            raise ValueError("schedule_not_found")
                        return self._result(
                            (schedule,),
                            datetime.now(UTC),
                            occurrences=self.repository.occurrences(
                                schedule.schedule_id, limit=limit
                            ),
                        )
            case UpdateScheduleCommand():
                return self._update(command, actor)
            case ChangeScheduleStateCommand():
                return self._change_state(command, actor)

    def _create(
        self, command: CreateScheduleCommand, invocation: ToolInvocation, actor: ScheduleActor
    ) -> DelegatedToolResult:
        if command.approval_mode is ScheduleApprovalMode.AUTO and not command.allowed_effects:
            raise ValueError("schedule_auto_effect_scope_required")
        now = datetime.now(UTC)
        self._validate_effects(command.allowed_effects, now=now)
        self._validate_resources(command.allowed_effects, command.allowed_resource_ids)
        schedule_id = "schedule-" + contract_sha256(
            {
                "workspace_id": actor.workspace_id,
                "member_id": actor.member_id,
                "run_id": invocation.run_id,
                "input_sha256": invocation.input_sha256,
            }
        )[:24]
        schedule = AgentSchedule(
            schedule_id=schedule_id,
            tenant_id=actor.workspace_id,
            authority=self._authority(actor),
            destination=ScheduleDestination(
                channel_id=actor.channel_id, thread_ts=actor.thread_ts
            ),
            goal=command.goal,
            budget=command.budget,
            rule=command.rule,
            timezone=command.timezone,
            approval_mode=command.approval_mode,
            misfire_policy=command.misfire_policy,
            allowed_effects=command.allowed_effects,
            allowed_resource_ids=command.allowed_resource_ids,
            max_occurrences=command.max_occurrences,
            starts_at=command.starts_at,
            ends_at=command.ends_at,
            created_at=now,
            updated_at=now,
        )
        return self._result((self.repository.create(schedule),), now)

    def _update(
        self, command: UpdateScheduleCommand, actor: ScheduleActor
    ) -> DelegatedToolResult:
        current = self.repository.get(actor.workspace_id, command.schedule_id)
        if current is None:
            raise ValueError("schedule_not_found")
        if current.status is ScheduleStatus.CANCELLED:
            raise ValueError("schedule_cancelled")
        now = datetime.now(UTC)
        if command.ends_at is not None and command.clear_ends_at:
            raise ValueError("schedule_end_update_conflict")
        if command.max_occurrences is not None and command.clear_max_occurrences:
            raise ValueError("schedule_occurrence_limit_update_conflict")
        updates: dict[
            str,
            ScheduleRule
            | ScheduleApprovalMode
            | ScheduleMisfirePolicy
            | tuple[str, ...]
            | AgentBudget
            | str
            | datetime
            | AgentGoal
            | ScheduleAuthority
            | ScheduleDestination
            | int
            | None,
        ] = {
            "revision": current.revision + 1,
            "updated_at": now,
            "authority": self._authority(actor),
        }
        if command.rule is not None:
            updates["rule"] = command.rule
        if command.timezone is not None:
            updates["timezone"] = command.timezone
        if command.starts_at is not None:
            updates["starts_at"] = command.starts_at
        if command.ends_at is not None:
            updates["ends_at"] = command.ends_at
        elif command.clear_ends_at:
            updates["ends_at"] = None
        if command.goal is not None:
            updates["goal"] = command.goal
        if command.approval_mode is not None:
            updates["approval_mode"] = command.approval_mode
        if command.misfire_policy is not None:
            updates["misfire_policy"] = command.misfire_policy
        if command.allowed_effects is not None:
            updates["allowed_effects"] = command.allowed_effects
        if command.allowed_resource_ids is not None:
            updates["allowed_resource_ids"] = command.allowed_resource_ids
        if command.budget is not None:
            updates["budget"] = command.budget
        if command.max_occurrences is not None:
            updates["max_occurrences"] = command.max_occurrences
        elif command.clear_max_occurrences:
            updates["max_occurrences"] = None
        if command.move_destination_to_current_conversation:
            updates["destination"] = ScheduleDestination(
                channel_id=actor.channel_id, thread_ts=actor.thread_ts
            )
        schedule = current.model_copy(update=updates)
        self._validate_effects(schedule.allowed_effects, now=now)
        self._validate_resources(
            schedule.allowed_effects, schedule.allowed_resource_ids
        )
        updated = self.repository.replace(
            schedule, expected_revision=command.expected_revision, actor_id=actor.member_id
        )
        return self._result((updated,), now)

    def _validate_effects(self, allowed_effects: tuple[str, ...], *, now: datetime) -> None:
        descriptors = {
            descriptor.capability_id: descriptor
            for descriptor in self.registry.current_descriptors(now=now)
        }
        for capability in allowed_effects:
            descriptor = descriptors.get(capability)
            if (
                capability == CAPABILITY
                or capability == "threads.connect"
                or descriptor is None
                or not descriptor.enabled
                or not descriptor.readiness.ready
                or descriptor.effect_class is EffectClass.OBSERVE
            ):
                raise ValueError("schedule_effect_scope_unavailable")

    @staticmethod
    def _validate_resources(
        allowed_effects: tuple[str, ...], allowed_resource_ids: tuple[str, ...]
    ) -> None:
        if (
            {
                "threads.publish",
                "threads.reply",
                "threads.draft.manage",
                "threads.account.configure",
            }
            & set(allowed_effects)
            and not allowed_resource_ids
        ):
            raise ValueError("schedule_threads_account_scope_required")

    def _change_state(
        self, command: ChangeScheduleStateCommand, actor: ScheduleActor
    ) -> DelegatedToolResult:
        current = self.repository.get(actor.workspace_id, command.schedule_id)
        if current is None:
            raise ValueError("schedule_not_found")
        if current.status is ScheduleStatus.CANCELLED:
            raise ValueError("schedule_cancelled")
        state = {
            "pause": ScheduleStatus.PAUSED,
            "resume": ScheduleStatus.ACTIVE,
            "cancel": ScheduleStatus.CANCELLED,
        }[command.action]
        now = datetime.now(UTC)
        updated = self.repository.replace(
            current.model_copy(
                update={
                    "status": state,
                    "authority": self._authority(actor),
                    "revision": current.revision + 1,
                    "updated_at": now,
                }
            ),
            expected_revision=command.expected_revision,
            actor_id=actor.member_id,
        )
        return self._result((updated,), now)

    @staticmethod
    def _authority(actor: ScheduleActor) -> ScheduleAuthority:
        return ScheduleAuthority(
            workspace_id=actor.workspace_id,
            member_id=actor.member_id,
            external_user_id=actor.external_user_id,
            conversation_id=actor.conversation_id,
            source_event_id=actor.source_event_id,
            source_sha256=actor.source_sha256,
        )

    @staticmethod
    def _result(
        schedules: tuple[AgentSchedule, ...],
        now: datetime,
        *,
        occurrences: tuple[ScheduleOccurrence, ...] = (),
    ) -> DelegatedToolResult:
        return DelegatedToolResult(
            disposition="succeeded",
            actual_cost_units=0,
            output={
                "schedules": [
                    {
                        **schedule.model_dump(mode="json"),
                        "next_occurrence": (
                            None
                            if (due := next_occurrence(schedule, after=now)) is None
                            else due.isoformat()
                        ),
                    }
                    for schedule in schedules
                ],
                "occurrences": [
                    occurrence.model_dump(mode="json") for occurrence in occurrences
                ],
            },
        )


def schedule_descriptor(*, now: datetime) -> ToolDescriptor:
    template = github_issue_descriptor(
        installation_id="installed:schedule", observed_at=now, ready=True
    )
    schema = _JSON.validate_python(_COMMAND.json_schema())
    return template.model_copy(
        update={
            "capability_id": CAPABILITY,
            "owner": "ads_booster.agent.service.schedule_repository",
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "effect_class": EffectClass.CONTROL_PLANE_WRITE,
            "cost": ToolCost(worst_case_units=0, unit="schedule_mutation"),
        }
    )


__all__ = [
    "CAPABILITY",
    "ScheduleActor",
    "ScheduleActorResolver",
    "ScheduleManagementTool",
    "schedule_descriptor",
]

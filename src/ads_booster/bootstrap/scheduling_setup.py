from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ads_booster.agent.core.registry import ToolRegistration, ToolRegistry
from ads_booster.agent.service.drive_work import DriveWorkQueue
from ads_booster.agent.service.schedule_repository import ScheduleRepository
from ads_booster.agent.service.schedule_runtime import ScheduleRuntime
from ads_booster.channels.slack_integration_actors import SlackIntegrationActors
from ads_booster.channels.task_results import result_for
from ads_booster.contracts.agent_run import AgentRun, AgentRunState
from ads_booster.contracts.agent_schedule import AgentSchedule, ScheduleOccurrence
from ads_booster.tools.compatibility import DelegatingToolAdapter
from ads_booster.tools.schedule_tools import CAPABILITY, ScheduleManagementTool, schedule_descriptor

if TYPE_CHECKING:
    from ads_booster.agent.service.application import MarketingAgentService
    from ads_booster.channels.slack_events import SlackEvents
    from ads_booster.contracts.tool_capability import ToolDescriptor


@dataclass(frozen=True, slots=True)
class SchedulingCatalog:
    tool: ScheduleManagementTool

    def registrations(self) -> tuple[ToolRegistration, ...]:
        return (
            ToolRegistration(
                capability_id=CAPABILITY,
                version="1",
                adapter=DelegatingToolAdapter(
                    capability_id=CAPABILITY,
                    version="1",
                    executor_id="agent-schedule",
                    executor=self.tool.execute,
                ),
                descriptor_factory=schedule_descriptor,
            ),
        )

    def descriptors(self, *, now: datetime) -> tuple[ToolDescriptor, ...]:
        return ToolRegistry.from_registrations(self.registrations(), now=now).descriptors


@dataclass(frozen=True, slots=True)
class SlackScheduleNotifier:
    events: SlackEvents

    def notify(
        self, schedule: AgentSchedule, occurrence: ScheduleOccurrence, run: AgentRun
    ) -> bool:
        result = result_for(
            run,
            self.events.commands.application.service.repository.records(
                run.tenant_id, run.run_id
            ),
        )
        notification_text = self.events.commands.summary(
            run.tenant_id, run.run_id, include_status=True
        )
        if run.state is AgentRunState.AWAITING_RECONCILIATION:
            notification_text += "\n" + result.text
        return self.events.store.enqueue_scheduled_notification(
            conversation_id=schedule.authority.conversation_id,
            event_id=occurrence.occurrence_id,
            external_user_id=schedule.authority.external_user_id,
            run_id=run.run_id,
            result=notification_text,
            task_result=result,
        )


def connect_scheduling(
    service: MarketingAgentService, events: SlackEvents, *, now: datetime
) -> ScheduleRuntime:
    database = service.repository.database_path
    actors = SlackIntegrationActors(str(database), events.identity)
    repository = ScheduleRepository(database)
    tool = ScheduleManagementTool(repository, actors.schedule_actor, service.registry)
    service.install_tool_catalog(SchedulingCatalog(tool), now=now)
    return ScheduleRuntime(
        repository=repository,
        service=service,
        drive_queue=DriveWorkQueue(database),
        authority=actors,
        notifier=SlackScheduleNotifier(events),
    )


__all__ = ["SchedulingCatalog", "connect_scheduling"]

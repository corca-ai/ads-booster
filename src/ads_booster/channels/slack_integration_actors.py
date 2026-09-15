from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable

from pydantic import TypeAdapter

from ads_booster.agent.service.schedule_repository import ScheduleRepository
from ads_booster.channels.contracts import ChannelIdentityBinding
from ads_booster.channels.slack_conversations import Conversation, Message
from ads_booster.contracts.agent_schedule import AgentSchedule
from ads_booster.contracts.threads import ThreadsActor
from ads_booster.knowledge.contract_types import ConversationEventKind
from ads_booster.knowledge.source_contracts import ConversationEvent
from ads_booster.threads.drafts import ThreadsDraftRepository
from ads_booster.tools.schedule_tools import ScheduleActor
from ads_booster.transport.json_types import JsonObject

_ROW: TypeAdapter[tuple[str, str] | None] = TypeAdapter(tuple[str, str] | None)
_EVENT_ROW: TypeAdapter[tuple[str] | None] = TypeAdapter(tuple[str] | None)


@dataclass(frozen=True, slots=True)
class SlackIntegrationActors:
    database_path: str
    authorize: Callable[[str], ChannelIdentityBinding]

    def schedule_actor(self, tenant_id: str, run_id: str) -> ScheduleActor:
        conversation, message = self._source(tenant_id, run_id)
        identity = self.authorize(message.user_id)
        if (
            identity.tenant_id != tenant_id
            or identity.revoked_at is not None
            or not identity.can_create_runs
            or not identity.can_approve
            or conversation.private
        ):
            raise ValueError("schedule_shared_conversation_required")
        return ScheduleActor(
            workspace_id=tenant_id,
            member_id=identity.member_id,
            external_user_id=identity.external_user_id,
            conversation_id=conversation.conversation_id,
            source_event_id=message.message_id,
            source_sha256=sha256(message.text.encode()).hexdigest(),
            channel_id=conversation.channel_id,
            thread_ts=conversation.thread_ts,
        )

    def threads_actor(self, tenant_id: str, run_id: str) -> ThreadsActor:
        conversation, message = self._source(tenant_id, run_id)
        identity = self.authorize(message.user_id)
        if identity.tenant_id != tenant_id or identity.revoked_at is not None:
            raise ValueError("threads_workspace_mismatch")
        schedule_scope = self._schedule_scope(tenant_id, run_id)
        return ThreadsActor(
            workspace_id=tenant_id,
            member_id=identity.member_id,
            conversation_id=conversation.conversation_id,
            source_event_id=message.message_id,
            scheduled=schedule_scope is not None,
            allowed_connection_ids=() if schedule_scope is None else schedule_scope,
        )

    def _schedule_scope(self, tenant_id: str, run_id: str) -> tuple[str, ...] | None:
        repository = ScheduleRepository(Path(self.database_path))
        occurrence = repository.occurrence_by_run(run_id)
        if occurrence is None:
            return None
        schedule = repository.get_for_occurrence(occurrence)
        if schedule.tenant_id != tenant_id:
            raise ValueError("threads_schedule_workspace_mismatch")
        return schedule.allowed_resource_ids

    def unavailable_reason(self, schedule: AgentSchedule, *, now: datetime) -> str | None:
        del now
        try:
            identity = self.authorize(schedule.authority.external_user_id)
        except ValueError:
            return "schedule_actor_revoked"
        if (
            identity.tenant_id != schedule.tenant_id
            or identity.revoked_at is not None
            or not identity.can_create_runs
            or not identity.can_approve
        ):
            return "schedule_workspace_changed"
        with closing(sqlite3.connect(self.database_path)) as database, database:
            row = _ROW.validate_python(
                database.execute(
                    """SELECT conversation.data_json,job.message_json
                    FROM slack_message_jobs AS job
                    JOIN slack_conversations AS conversation
                    ON conversation.conversation_id=job.conversation_id
                    WHERE job.message_id=? AND job.conversation_id=?""",
                    (
                        schedule.authority.source_event_id,
                        schedule.authority.conversation_id,
                    ),
                ).fetchone()
            )
            event_row = _EVENT_ROW.validate_python(
                database.execute(
                    """SELECT event_json FROM knowledge_conversation_events
                    WHERE message_id=? ORDER BY revision DESC LIMIT 1""",
                    (schedule.authority.source_event_id,),
                ).fetchone()
            )
        if row is None:
            return "schedule_authorization_source_missing"
        conversation = Conversation.model_validate_json(row[0])
        message = Message.model_validate_json(row[1])
        if event_row is None:
            return "schedule_authorization_event_missing"
        event = ConversationEvent.model_validate_json(event_row[0])
        if event.event_kind is not ConversationEventKind.MESSAGE_FINALIZED:
            return "schedule_authorization_source_changed"
        if (
            conversation.closed
            or conversation.private
            or conversation.tenant_id != schedule.tenant_id
        ):
            return "schedule_conversation_unavailable"
        if message.user_id != schedule.authority.external_user_id:
            return "schedule_authorization_actor_changed"
        if (
            event.text != message.text
            or sha256(event.text.encode()).hexdigest() != schedule.authority.source_sha256
        ):
            return "schedule_authorization_source_changed"
        return None

    def effect_unavailable_reason(
        self,
        schedule: AgentSchedule,
        capability_id: str,
        invocation_input: JsonObject,
        *,
        now: datetime,
    ) -> str | None:
        del now
        if capability_id not in {
            "threads.publish",
            "threads.reply",
            "threads.draft.manage",
            "threads.account.configure",
        }:
            return None
        allowed = set(schedule.allowed_resource_ids)
        if not allowed:
            return "schedule_threads_account_scope_missing"
        if capability_id == "threads.account.configure":
            connection_id = invocation_input.get("connection_id")
            return (
                None
                if isinstance(connection_id, str) and connection_id in allowed
                else "schedule_threads_account_denied"
            )
        action = invocation_input.get("action")
        if capability_id == "threads.draft.manage" and isinstance(action, str) and action in {
            "create",
            "replace",
        }:
            items = invocation_input.get("items")
            if not isinstance(items, list):
                return "schedule_threads_draft_scope_invalid"
            selected: set[str] = set()
            for item in items:
                if not isinstance(item, dict):
                    return "schedule_threads_draft_scope_invalid"
                connection_id = item.get("connection_id")
                if not isinstance(connection_id, str):
                    return "schedule_threads_draft_scope_invalid"
                selected.add(connection_id)
            return None if selected and selected <= allowed else "schedule_threads_account_denied"
        batch_id = invocation_input.get("batch_id")
        if not isinstance(batch_id, str):
            return "schedule_threads_draft_scope_invalid"
        batch = ThreadsDraftRepository(Path(self.database_path)).get(
            schedule.tenant_id, batch_id
        )
        if batch is None or batch.owner_member_id != schedule.authority.member_id:
            return "schedule_threads_draft_unavailable"
        if capability_id == "threads.draft.manage":
            selected = {item.connection_id for item in batch.items}
            return (
                None
                if selected and selected <= allowed
                else "schedule_threads_account_denied"
            )
        item_ids_value = invocation_input.get("item_ids")
        if not isinstance(item_ids_value, list):
            return "schedule_threads_draft_scope_invalid"
        item_ids: set[str] = set()
        for item_id in item_ids_value:
            if not isinstance(item_id, str):
                return "schedule_threads_draft_scope_invalid"
            item_ids.add(item_id)
        selected = {
            item.connection_id
            for item in batch.items
            if item.item_id in item_ids
        }
        return None if selected and selected <= allowed else "schedule_threads_account_denied"

    def _source(self, tenant_id: str, run_id: str) -> tuple[Conversation, Message]:
        with closing(sqlite3.connect(self.database_path)) as database, database:
            row = _ROW.validate_python(
                database.execute(
                    """SELECT conversation.data_json,job.message_json
                    FROM slack_message_jobs AS job
                    JOIN slack_conversations AS conversation
                    ON conversation.conversation_id=job.conversation_id
                    WHERE json_extract(conversation.data_json,'$.tenant_id')=?
                    AND (json_extract(job.plan_json,'$.run_id')=?
                         OR json_extract(job.message_json,'$.result_run_id')=?)
                    ORDER BY job.rowid DESC LIMIT 1""",
                    (tenant_id, run_id, run_id),
                ).fetchone()
            )
            if row is None:
                row = _ROW.validate_python(
                    database.execute(
                        """SELECT conversation.data_json,job.message_json
                        FROM agent_schedule_occurrences AS occurrence
                        JOIN agent_schedules AS schedule
                        ON schedule.schedule_id=occurrence.schedule_id
                        JOIN slack_message_jobs AS job
                        ON job.message_id=json_extract(
                            schedule.schedule_json,'$.authority.source_event_id'
                        )
                        JOIN slack_conversations AS conversation
                        ON conversation.conversation_id=job.conversation_id
                        WHERE schedule.tenant_id=? AND occurrence.run_id=?""",
                        (tenant_id, run_id),
                    ).fetchone()
                )
        if row is None:
            raise ValueError("slack_run_source_not_found")
        return Conversation.model_validate_json(row[0]), Message.model_validate_json(row[1])


__all__ = ["SlackIntegrationActors"]

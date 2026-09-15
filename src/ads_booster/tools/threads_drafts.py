from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.models import ContractModel, CountryCode, Identifier
from ads_booster.contracts.threads import ThreadsActor
from ads_booster.contracts.tool_capability import EffectClass, ToolCost, ToolDescriptor
from ads_booster.threads.accounts import ThreadsAccountRepository
from ads_booster.threads.drafts import (
    ThreadsAssetReference,
    ThreadsDraftAction,
    ThreadsDraftBatch,
    ThreadsDraftItem,
    ThreadsDraftRepository,
    ThreadsDraftState,
)
from ads_booster.threads.publications import ThreadsPublicationRepository
from ads_booster.tools.compatibility import DelegatedToolResult
from ads_booster.tools.descriptors import github_issue_descriptor
from ads_booster.tools.threads_tools import ThreadsActorResolver
from ads_booster.transport.json_types import JsonObject

CAPABILITY = "threads.draft.manage"


class DraftItemInput(ContractModel):
    item_id: Identifier | None = None
    action: ThreadsDraftAction
    connection_id: str
    text: Annotated[str, Field(min_length=1, max_length=500)]
    country: CountryCode | None = None
    reply_to_id: str | None = None
    assets: Annotated[tuple[ThreadsAssetReference, ...], Field(max_length=20)] = ()
    excluded: bool = False


class CreateDraftInput(ContractModel):
    action: Literal["create"]
    items: Annotated[tuple[DraftItemInput, ...], Field(min_length=1, max_length=50)]


class GetDraftInput(ContractModel):
    action: Literal["get"]
    batch_id: str


class ReplaceDraftInput(ContractModel):
    action: Literal["replace"]
    batch_id: str
    expected_revision: Annotated[int, Field(ge=1)]
    items: Annotated[tuple[DraftItemInput, ...], Field(min_length=1, max_length=50)]


class DraftStateInput(ContractModel):
    action: Literal["approve", "cancel"]
    batch_id: str
    expected_revision: Annotated[int, Field(ge=1)]


type ThreadsDraftCommand = CreateDraftInput | GetDraftInput | ReplaceDraftInput | DraftStateInput
_COMMAND: TypeAdapter[ThreadsDraftCommand] = TypeAdapter(ThreadsDraftCommand)
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class ThreadsDraftTool:
    drafts: ThreadsDraftRepository
    accounts: ThreadsAccountRepository
    actors: ThreadsActorResolver
    publications: ThreadsPublicationRepository

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        try:
            return self._execute(invocation, descriptor)
        except ValueError as error:
            code = str(error)
            if not code.startswith("threads_") or "descriptor_mismatch" in code:
                raise
            return DelegatedToolResult(
                disposition="failed", actual_cost_units=0, output={"error": code[:160]}
            )

    def _execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        if descriptor.capability_id != CAPABILITY or invocation.tenant_id is None:
            raise ValueError("threads_draft_descriptor_mismatch")
        actor = self.actors(invocation.tenant_id, invocation.run_id)
        command = _COMMAND.validate_python(invocation.input)
        self._require_actor_scope(command, actor)
        match command:
            case CreateDraftInput():
                batch = self._create(command, invocation, actor)
            case GetDraftInput(batch_id=batch_id):
                batch = self._required(actor.workspace_id, batch_id, actor.member_id)
            case ReplaceDraftInput():
                batch = self._replace(command, actor.workspace_id, actor.member_id)
            case DraftStateInput():
                batch = self._state(command, actor.workspace_id, actor.member_id)
        return DelegatedToolResult(
            disposition="succeeded",
            actual_cost_units=0,
            output={
                "batch": batch.model_dump(mode="json"),
                "publications": [
                    receipt.model_dump(mode="json")
                    for receipt in self.publications.list_for_batch(batch.batch_id)
                ],
            },
        )

    def _require_actor_scope(
        self, command: ThreadsDraftCommand, actor: ThreadsActor
    ) -> None:
        if not actor.scheduled:
            return
        connection_ids: set[str]
        match command:
            case CreateDraftInput(items=items) | ReplaceDraftInput(items=items):
                connection_ids = {item.connection_id for item in items}
            case GetDraftInput(batch_id=batch_id) | DraftStateInput(batch_id=batch_id):
                batch = self.drafts.get(actor.workspace_id, batch_id)
                if batch is None:
                    connection_ids = set()
                else:
                    connection_ids = {item.connection_id for item in batch.items}
        if not connection_ids or not connection_ids <= set(actor.allowed_connection_ids):
            raise ValueError("threads_scheduled_account_denied")

    def _create(
        self,
        command: CreateDraftInput,
        invocation: ToolInvocation,
        actor: ThreadsActor,
    ) -> ThreadsDraftBatch:
        now = datetime.now(UTC)
        batch_id = "threads-draft-" + contract_sha256(
            {
                "workspace_id": actor.workspace_id,
                "member_id": actor.member_id,
                "run_id": invocation.run_id,
                "input_sha256": invocation.input_sha256,
            }
        )[:20]
        batch = ThreadsDraftBatch(
            batch_id=batch_id,
            workspace_id=actor.workspace_id,
            owner_member_id=actor.member_id,
            conversation_id=actor.conversation_id,
            source_event_id=actor.source_event_id,
            items=self._items(
                command.items, actor.workspace_id, actor.member_id, batch_id
            ),
            created_at=now,
            updated_at=now,
        )
        return self.drafts.create(batch)

    def _replace(
        self, command: ReplaceDraftInput, workspace_id: str, member_id: str
    ) -> ThreadsDraftBatch:
        current = self._required(workspace_id, command.batch_id, member_id)
        self._require_not_dispatched(current)
        if current.state not in {ThreadsDraftState.DRAFT, ThreadsDraftState.APPROVED}:
            raise ValueError("threads_draft_not_editable")
        now = datetime.now(UTC)
        replacement = current.model_copy(
            update={
                "items": self._replacement_items(command.items, current, member_id),
                "revision": current.revision + 1,
                "state": ThreadsDraftState.DRAFT,
                "updated_at": now,
            }
        )
        return self.drafts.replace(
            replacement, expected_revision=command.expected_revision, actor_id=member_id
        )

    def _state(
        self, command: DraftStateInput, workspace_id: str, member_id: str
    ) -> ThreadsDraftBatch:
        current = self._required(workspace_id, command.batch_id, member_id)
        self._require_not_dispatched(current)
        if command.action == "approve" and current.state is not ThreadsDraftState.DRAFT:
            raise ValueError("threads_draft_not_approvable")
        if command.action == "cancel" and current.state not in {
            ThreadsDraftState.DRAFT,
            ThreadsDraftState.APPROVED,
        }:
            raise ValueError("threads_draft_not_cancellable")
        state = (
            ThreadsDraftState.APPROVED
            if command.action == "approve"
            else ThreadsDraftState.CANCELLED
        )
        updated = current.model_copy(
            update={
                "revision": current.revision + 1,
                "state": state,
                "updated_at": datetime.now(UTC),
            }
        )
        return self.drafts.replace(
            updated, expected_revision=command.expected_revision, actor_id=member_id
        )

    def _items(
        self,
        items: tuple[DraftItemInput, ...],
        workspace_id: str,
        member_id: str,
        batch_id: str,
    ) -> tuple[ThreadsDraftItem, ...]:
        result: list[ThreadsDraftItem] = []
        for index, item in enumerate(items, start=1):
            _ = self.accounts.require_owner(workspace_id, item.connection_id, member_id)
            item_id = item.item_id or f"{batch_id}-item-{index}"
            if any(existing.item_id == item_id for existing in result):
                raise ValueError("threads_draft_item_id_duplicate")
            result.append(
                ThreadsDraftItem(
                    item_id=item_id,
                    action=item.action,
                    connection_id=item.connection_id,
                    country=item.country,
                    text=item.text,
                    reply_to_id=item.reply_to_id,
                    assets=item.assets,
                    excluded=item.excluded,
                )
            )
        return tuple(result)

    def _replacement_items(
        self,
        items: tuple[DraftItemInput, ...],
        current: ThreadsDraftBatch,
        member_id: str,
    ) -> tuple[ThreadsDraftItem, ...]:
        known = {item.item_id for item in current.items}
        if any(item.item_id is None for item in items):
            raise ValueError("threads_draft_replacement_item_id_required")
        replacement = self._items(
            items, current.workspace_id, member_id, current.batch_id
        )
        replacement_ids = {item.item_id for item in replacement}
        if replacement_ids != known:
            raise ValueError("threads_draft_replacement_identity_changed")
        return replacement

    def _require_not_dispatched(self, batch: ThreadsDraftBatch) -> None:
        if self.publications.list_for_batch(
            batch.batch_id, draft_revision=batch.revision
        ):
            raise ValueError("threads_draft_already_dispatched")

    def _required(self, workspace_id: str, batch_id: str, member_id: str) -> ThreadsDraftBatch:
        batch = self.drafts.get(workspace_id, batch_id)
        if batch is None or batch.owner_member_id != member_id:
            raise ValueError("threads_draft_not_found")
        return batch


def descriptor(*, now: datetime) -> ToolDescriptor:
    template = github_issue_descriptor(
        installation_id="configured:threads", observed_at=now, ready=True
    )
    schema = _JSON.validate_python(_COMMAND.json_schema())
    return template.model_copy(
        update={
            "capability_id": CAPABILITY,
            "owner": "ads_booster.tools.threads_drafts",
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "effect_class": EffectClass.CONTROL_PLANE_WRITE,
            "cost": ToolCost(worst_case_units=0, unit="threads_draft"),
        }
    )


__all__ = ["CAPABILITY", "ThreadsDraftTool", "descriptor"]

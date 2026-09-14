from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.threads import (
    ThreadsAccount,
    ThreadsAccountStatus,
    ThreadsActor,
    ThreadsPublicationReceipt,
)
from ads_booster.contracts.tool_capability import (
    ToolCost,
    ToolDescriptor,
    ToolReconciliationPolicy,
)
from ads_booster.learning.provider_metrics import compare_provider_metrics
from ads_booster.providers.threads_api import ThreadsApiError
from ads_booster.threads.accounts import ThreadsAccountRepository, ThreadsTokenVault
from ads_booster.threads.drafts import ThreadsDraftAction
from ads_booster.threads.metrics import ThreadsMetricsService
from ads_booster.threads.publications import ThreadsPublisher, publication_operation_id
from ads_booster.tools.compatibility import DelegatedToolResult
from ads_booster.tools.descriptors import github_issue_descriptor, research_descriptor
from ads_booster.transport.json_types import JsonObject

READ_CAPABILITIES = frozenset(
    {
        "threads.accounts.list",
        "threads.search",
        "threads.posts.list",
        "threads.post.get",
        "threads.replies",
        "threads.conversation",
        "threads.metrics.collect",
        "threads.metrics.series",
        "threads.publication.get",
    }
)
WRITE_CAPABILITIES = frozenset({"threads.publish", "threads.reply"})


class ThreadsActorResolver(Protocol):
    def __call__(self, tenant_id: str, run_id: str) -> ThreadsActor: ...


class AccountsInput(ContractModel):
    action: Literal["accounts"]
    scope: Literal["mine", "workspace"] = "mine"


class SearchInput(ContractModel):
    action: Literal["search"]
    connection_id: str
    query: Annotated[str, Field(min_length=1, max_length=500)]
    search_type: Literal["TOP", "RECENT"] = "RECENT"
    limit: Annotated[int, Field(ge=1, le=25)] = 25
    after: str | None = None


class PostInput(ContractModel):
    action: Literal["post"]
    connection_id: str
    post_id: str


class PostsInput(ContractModel):
    action: Literal["posts"]
    connection_id: str
    limit: Annotated[int, Field(ge=1, le=25)] = 25
    after: str | None = None


class ConversationInput(ContractModel):
    action: Literal["conversation"]
    connection_id: str
    post_id: str
    limit: Annotated[int, Field(ge=1, le=25)] = 25
    after: str | None = None


class RepliesInput(ContractModel):
    action: Literal["replies"]
    connection_id: str
    post_id: str
    limit: Annotated[int, Field(ge=1, le=25)] = 25
    after: str | None = None


class MetricsInput(ContractModel):
    action: Literal["metrics"]
    connection_id: str
    subject_kind: Literal["account", "post"]
    subject_id: str | None = None
    metrics: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]


class MetricSeriesInput(ContractModel):
    action: Literal["metric_series"]
    connection_id: str
    subject_kind: Literal["account", "post"]
    subject_id: str | None = None
    metric: str
    limit: Annotated[int, Field(ge=1, le=1000)] = 100


class PublicationInput(ContractModel):
    action: Literal["publication"]
    operation_id: str


class PublishInput(ContractModel):
    action: Literal["publish"]
    batch_id: str
    batch_revision: Annotated[int, Field(ge=1)]
    item_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=50)]


class ReplyPublishInput(ContractModel):
    action: Literal["reply"]
    batch_id: str
    batch_revision: Annotated[int, Field(ge=1)]
    item_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=50)]


type ThreadsToolInput = Annotated[
    AccountsInput
    | SearchInput
    | PostInput
    | PostsInput
    | ConversationInput
    | RepliesInput
    | MetricsInput
    | MetricSeriesInput
    | PublicationInput
    | PublishInput
    | ReplyPublishInput,
    Field(discriminator="action"),
]

_INPUT: TypeAdapter[ThreadsToolInput] = TypeAdapter(ThreadsToolInput)
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class ThreadsTools:
    accounts: ThreadsAccountRepository
    tokens: ThreadsTokenVault
    metrics: ThreadsMetricsService
    publisher: ThreadsPublisher
    actors: ThreadsActorResolver

    def approval_allowed(
        self,
        invocation: ToolInvocation,
        descriptor: ToolDescriptor,
        approver_id: str,
    ) -> bool:
        if descriptor.capability_id not in WRITE_CAPABILITIES:
            return True
        if invocation.tenant_id is None:
            return False
        request = _INPUT.validate_python(invocation.input)
        match request:
            case PublishInput(batch_id=batch_id) | ReplyPublishInput(batch_id=batch_id):
                batch = self.publisher.drafts.get(invocation.tenant_id, batch_id)
                return batch is not None and batch.owner_member_id == approver_id
            case _:
                return False

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        try:
            return self._execute(invocation, descriptor)
        except ThreadsApiError as error:
            return DelegatedToolResult(
                disposition="failed",
                actual_cost_units=1,
                output={
                    "error": "threads_api_request_failed",
                    "status": error.status,
                    "code": error.code,
                    "retry_after_seconds": error.retry_after_seconds,
                },
            )
        except ValueError as error:
            code = str(error)
            if not code.startswith("threads_") or "descriptor_mismatch" in code:
                raise
            return DelegatedToolResult(
                disposition="failed",
                actual_cost_units=0,
                output={"error": code[:160]},
            )

    def _execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        if invocation.tenant_id is None:
            raise ValueError("threads_tenant_required")
        capability = descriptor.capability_id
        if capability not in READ_CAPABILITIES | WRITE_CAPABILITIES:
            raise ValueError("threads_descriptor_mismatch")
        actor = self.actors(invocation.tenant_id, invocation.run_id)
        request = _INPUT.validate_python(invocation.input)
        match request:
            case AccountsInput(scope=scope):
                if capability != "threads.accounts.list":
                    raise ValueError("threads_descriptor_mismatch")
                return self._accounts(actor, scope=scope)
            case SearchInput():
                if capability != "threads.search":
                    raise ValueError("threads_descriptor_mismatch")
                account, token = self._read_account(actor, request.connection_id)
                self._require_scope(account.granted_scopes, "threads_keyword_search")
                with self._account_request(account):
                    page = self.publisher.api.search(
                        token,
                        query=request.query,
                        search_type=request.search_type,
                        limit=request.limit,
                        after=request.after,
                        account_id=account.connection_id,
                    )
                return _observed(
                    {"posts": [post.model_dump(mode="json") for post in page.posts], "after": page.after}
                )
            case PostInput():
                if capability != "threads.post.get":
                    raise ValueError("threads_descriptor_mismatch")
                account, token = self._read_account(actor, request.connection_id)
                with self._account_request(account):
                    post = self.publisher.api.post(
                        token, request.post_id, account_id=account.connection_id
                    )
                return _observed(post.model_dump(mode="json"))
            case PostsInput():
                if capability != "threads.posts.list":
                    raise ValueError("threads_descriptor_mismatch")
                account, token = self._read_account(actor, request.connection_id)
                with self._account_request(account):
                    page = self.publisher.api.posts(
                        token,
                        account_id=account.connection_id,
                        limit=request.limit,
                        after=request.after,
                    )
                return _observed(
                    {"posts": [post.model_dump(mode="json") for post in page.posts], "after": page.after}
                )
            case ConversationInput() | RepliesInput():
                expected = (
                    "threads.conversation"
                    if request.action == "conversation"
                    else "threads.replies"
                )
                if capability != expected:
                    raise ValueError("threads_descriptor_mismatch")
                account, token = self._read_account(actor, request.connection_id)
                self._require_scope(account.granted_scopes, "threads_read_replies")
                with self._account_request(account):
                    page = (
                        self.publisher.api.conversation(
                            token,
                            post_id=request.post_id,
                            account_id=account.connection_id,
                            limit=request.limit,
                            after=request.after,
                        )
                        if request.action == "conversation"
                        else self.publisher.api.replies(
                            token,
                            post_id=request.post_id,
                            account_id=account.connection_id,
                            limit=request.limit,
                            after=request.after,
                        )
                    )
                return _observed(
                    {"posts": [post.model_dump(mode="json") for post in page.posts], "after": page.after}
                )
            case MetricsInput():
                if capability != "threads.metrics.collect":
                    raise ValueError("threads_descriptor_mismatch")
                account, _ = self._read_account(actor, request.connection_id)
                self._require_scope(account.granted_scopes, "threads_manage_insights")
                subject_id = (
                    account.provider_account_id
                    if request.subject_kind == "account"
                    else request.subject_id
                )
                if subject_id is None:
                    raise ValueError("threads_metric_post_id_required")
                snapshots = self.metrics.collect(
                    workspace_id=actor.workspace_id,
                    connection_id=request.connection_id,
                    subject_kind=request.subject_kind,
                    subject_id=subject_id,
                    metric_names=request.metrics,
                    observed_at=datetime.now(UTC),
                )
                return _observed(
                    {"snapshots": [item.model_dump(mode="json") for item in snapshots]}
                )
            case MetricSeriesInput():
                if capability != "threads.metrics.series":
                    raise ValueError("threads_descriptor_mismatch")
                account = self._metadata_account(actor, request.connection_id)
                subject_id = (
                    account.provider_account_id
                    if request.subject_kind == "account"
                    else request.subject_id
                )
                if subject_id is None:
                    raise ValueError("threads_metric_post_id_required")
                series = self.metrics.metrics.series(
                    workspace_id=actor.workspace_id,
                    connection_id=request.connection_id,
                    subject_kind=request.subject_kind,
                    subject_id=subject_id,
                    metric=request.metric,
                    limit=request.limit,
                )
                changes = tuple(
                    compare_provider_metrics(previous, current)
                    for current, previous in zip(series, series[1:], strict=False)
                )
                return _observed(
                    {
                        "snapshots": [item.model_dump(mode="json") for item in series],
                        "changes": [item.model_dump(mode="json") for item in changes],
                    }
                )
            case PublicationInput():
                if capability != "threads.publication.get":
                    raise ValueError("threads_descriptor_mismatch")
                current_receipt = self.publisher.publications.get(request.operation_id)
                if (
                    actor.scheduled
                    and (
                        current_receipt is None
                        or current_receipt.connection_id not in actor.allowed_connection_ids
                    )
                ):
                    raise ValueError("threads_scheduled_account_denied")
                receipt = self.publisher.reconcile(
                    operation_id=request.operation_id,
                    workspace_id=actor.workspace_id,
                    member_id=actor.member_id,
                    now=datetime.now(UTC),
                )
                return _observed({"publication": receipt.model_dump(mode="json")})
            case PublishInput() | ReplyPublishInput():
                if capability not in WRITE_CAPABILITIES:
                    raise ValueError("threads_descriptor_mismatch")
                if capability != f"threads.{request.action}":
                    raise ValueError("threads_descriptor_mismatch")
                expected_action = (
                    ThreadsDraftAction.PUBLISH
                    if capability == "threads.publish"
                    else ThreadsDraftAction.REPLY
                )
                self._require_publication_resources(
                    actor,
                    request.batch_id,
                    request.batch_revision,
                    request.item_ids,
                    expected_action,
                )
                invocation_sha256 = contract_sha256(invocation)
                receipts: list[ThreadsPublicationReceipt] = []
                for item_id in request.item_ids:
                    receipt = self.publisher.publish(
                        operation_id=publication_operation_id(invocation_sha256, item_id),
                        workspace_id=actor.workspace_id,
                        member_id=actor.member_id,
                        batch_id=request.batch_id,
                        batch_revision=request.batch_revision,
                        item_id=item_id,
                        expected_action=expected_action,
                        run_id=invocation.run_id,
                        invocation_sha256=invocation_sha256,
                        now=datetime.now(UTC),
                    )
                    receipts.append(receipt)
                    if receipt.state == "uncertain":
                        break
                attempted_ids = {receipt.item_id for receipt in receipts}
                unattempted = [
                    item_id for item_id in request.item_ids if item_id not in attempted_ids
                ]
                if any(receipt.state == "uncertain" for receipt in receipts):
                    disposition = "unknown_side_effect"
                elif unattempted or any(receipt.state != "published" for receipt in receipts):
                    disposition = "failed"
                else:
                    disposition = "succeeded"
                output = _JSON.validate_python(
                    {
                        "publications": [
                            receipt.model_dump(mode="json") for receipt in receipts
                        ],
                        "requested_item_ids": list(request.item_ids),
                        "unattempted_item_ids": unattempted,
                    }
                )
                return DelegatedToolResult(
                    disposition=disposition,
                    actual_cost_units=len(receipts),
                    output=output,
                )

    def _accounts(
        self, actor: ThreadsActor, *, scope: Literal["mine", "workspace"]
    ) -> DelegatedToolResult:
        accounts = self.accounts.list_for_workspace(
            actor.workspace_id,
            owner_member_id=actor.member_id if scope == "mine" else None,
        )
        if actor.scheduled:
            accounts = tuple(
                account
                for account in accounts
                if account.connection_id in actor.allowed_connection_ids
            )
        return _observed(
            {
                "accounts": [
                    account.model_dump(
                        mode="json",
                        exclude={
                            "token_ref",
                            "provider_account_id",
                            *(
                                ()
                                if account.owner_member_id == actor.member_id
                                else ("concept", "tone", "references")
                            ),
                        },
                    )
                    for account in accounts
                ]
            }
        )

    def _read_account(
        self, actor: ThreadsActor, connection_id: str
    ) -> tuple[ThreadsAccount, str]:
        if actor.scheduled and connection_id not in actor.allowed_connection_ids:
            raise ValueError("threads_scheduled_account_denied")
        account = self.accounts.require_readable(actor.workspace_id, connection_id)
        return account, self.tokens.get(account.token_ref)

    def _metadata_account(
        self, actor: ThreadsActor, connection_id: str
    ) -> ThreadsAccount:
        if actor.scheduled and connection_id not in actor.allowed_connection_ids:
            raise ValueError("threads_scheduled_account_denied")
        account = self.accounts.get(actor.workspace_id, connection_id)
        if account is None:
            raise ValueError("threads_account_not_found")
        return account

    def _require_publication_resources(
        self,
        actor: ThreadsActor,
        batch_id: str,
        batch_revision: int,
        item_ids: tuple[str, ...],
        expected_action: ThreadsDraftAction,
    ) -> None:
        batch = self.publisher.drafts.get(actor.workspace_id, batch_id)
        selected = (
            ()
            if batch is None
            else tuple(item for item in batch.items if item.item_id in item_ids)
        )
        if (
            batch is None
            or batch.revision != batch_revision
            or batch.owner_member_id != actor.member_id
            or len(selected) != len(item_ids)
            or len(set(item_ids)) != len(item_ids)
            or any(item.excluded or item.action is not expected_action for item in selected)
            or any(
                actor.scheduled
                and item.connection_id not in actor.allowed_connection_ids
                for item in selected
            )
        ):
            raise ValueError("threads_publication_batch_invalid")
        for item in selected:
            account = self.accounts.require_owner(
                actor.workspace_id, item.connection_id, actor.member_id
            )
            _ = self.accounts.require_readable(
                actor.workspace_id, item.connection_id
            )
            self._require_scope(account.granted_scopes, "threads_content_publish")

    @contextmanager
    def _account_request(self, account: ThreadsAccount) -> Generator[None]:
        try:
            yield
        except ThreadsApiError as error:
            if error.status == 401:
                _ = self.accounts.put(
                    account.model_copy(
                        update={
                            "status": ThreadsAccountStatus.REAUTH_REQUIRED,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
            raise

    @staticmethod
    def _require_scope(granted: tuple[str, ...], required: str) -> None:
        if required not in granted:
            raise ValueError("threads_scope_unavailable")


def descriptors(*, now: datetime) -> tuple[ToolDescriptor, ...]:
    result: list[ToolDescriptor] = []
    for capability in sorted(READ_CAPABILITIES | WRITE_CAPABILITIES):
        input_schema = _input_schema(capability)
        if capability in READ_CAPABILITIES:
            template = research_descriptor(
                installation_id="configured:threads", observed_at=now, ready=True
            )
            cost = ToolCost(worst_case_units=1, unit="threads_read")
        else:
            template = github_issue_descriptor(
                installation_id="configured:threads", observed_at=now, ready=True
            )
            cost = ToolCost(worst_case_units=50, unit="threads_write")
        result.append(
            template.model_copy(
                update={
                    "capability_id": capability,
                    "owner": "ads_booster.tools.threads_tools",
                    "input_schema": input_schema,
                    "input_schema_sha256": contract_sha256(input_schema),
                    "cost": cost,
                    **(
                        {
                            "reconciliation": ToolReconciliationPolicy(
                                mode="readback",
                                lookup_capability_id="threads.publication.get",
                                terminal_dispositions=(
                                    "succeeded",
                                    "failed",
                                    "unknown_side_effect",
                                ),
                            )
                        }
                        if capability in WRITE_CAPABILITIES
                        else {}
                    ),
                }
            )
        )
    return tuple(result)


def _input_schema(capability: str) -> JsonObject:
    match capability:
        case "threads.accounts.list":
            model = AccountsInput
        case "threads.search":
            model = SearchInput
        case "threads.posts.list":
            model = PostsInput
        case "threads.post.get":
            model = PostInput
        case "threads.replies":
            model = RepliesInput
        case "threads.conversation":
            model = ConversationInput
        case "threads.metrics.collect":
            model = MetricsInput
        case "threads.metrics.series":
            model = MetricSeriesInput
        case "threads.publication.get":
            model = PublicationInput
        case "threads.publish":
            model = PublishInput
        case "threads.reply":
            model = ReplyPublishInput
        case _:
            raise ValueError("threads_capability_unknown")
    return _JSON.validate_python(model.model_json_schema())


def _observed(output: JsonObject) -> DelegatedToolResult:
    return DelegatedToolResult(disposition="no_effect", actual_cost_units=1, output=output)


__all__ = ["READ_CAPABILITIES", "ThreadsActor", "ThreadsActorResolver", "ThreadsTools", "descriptors"]

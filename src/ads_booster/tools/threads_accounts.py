from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.models import ContractModel, CountryCode, Identifier
from ads_booster.contracts.threads import ThreadsAccountReference
from ads_booster.contracts.tool_capability import EffectClass, ToolCost, ToolDescriptor
from ads_booster.providers.threads_api import ThreadsApiError
from ads_booster.threads.accounts import ThreadsAccountRepository
from ads_booster.threads.oauth import ThreadsOAuthService
from ads_booster.tools.compatibility import DelegatedToolResult
from ads_booster.tools.descriptors import github_issue_descriptor
from ads_booster.tools.threads_tools import ThreadsActorResolver
from ads_booster.transport.json_types import JsonObject

CAPABILITY = "threads.account.configure"


class ThreadsAccountProfileInput(ContractModel):
    action: Literal["configure"] = "configure"
    connection_id: Identifier
    country: CountryCode | None = None
    concept: Annotated[str, Field(max_length=2_000)] | None = None
    tone: Annotated[str, Field(max_length=2_000)] | None = None
    references: Annotated[tuple[ThreadsAccountReference, ...], Field(max_length=100)] | None = None


class ThreadsAccountDisconnectInput(ContractModel):
    action: Literal["disconnect"]
    connection_id: Identifier


class ThreadsAccountRefreshInput(ContractModel):
    action: Literal["refresh"]
    connection_id: Identifier


type ThreadsAccountCommand = Annotated[
    ThreadsAccountProfileInput | ThreadsAccountDisconnectInput | ThreadsAccountRefreshInput,
    Field(discriminator="action"),
]
_COMMAND: TypeAdapter[ThreadsAccountCommand] = TypeAdapter(ThreadsAccountCommand)


_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class ThreadsAccountProfileTool:
    accounts: ThreadsAccountRepository
    actors: ThreadsActorResolver
    oauth: ThreadsOAuthService

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> DelegatedToolResult:
        try:
            return self._execute(invocation, descriptor)
        except ThreadsApiError as error:
            return DelegatedToolResult(
                disposition="failed",
                actual_cost_units=0,
                output={
                    "error": "threads_token_refresh_failed",
                    "status": error.status,
                    "code": error.code,
                },
            )
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
            raise ValueError("threads_account_profile_descriptor_mismatch")
        actor = self.actors(invocation.tenant_id, invocation.run_id)
        request = _COMMAND.validate_python(invocation.input)
        if actor.scheduled and request.connection_id not in actor.allowed_connection_ids:
            raise ValueError("threads_scheduled_account_denied")
        now = datetime.now(UTC)
        match request:
            case ThreadsAccountDisconnectInput():
                updated = self.oauth.disconnect_account(
                    actor.workspace_id,
                    request.connection_id,
                    actor.member_id,
                    now=now,
                )
            case ThreadsAccountRefreshInput():
                updated = self.oauth.refresh_account(
                    actor.workspace_id,
                    request.connection_id,
                    actor.member_id,
                    now=now,
                )
            case ThreadsAccountProfileInput():
                account = self.accounts.require_owner(
                    actor.workspace_id, request.connection_id, actor.member_id
                )
                fields = request.model_fields_set - {"action", "connection_id"}
                if not fields:
                    raise ValueError("threads_account_profile_change_required")
                changes: dict[str, object] = {"updated_at": now}
                if "country" in fields:
                    changes["country"] = request.country
                if "concept" in fields:
                    changes["concept"] = request.concept or ""
                if "tone" in fields:
                    changes["tone"] = request.tone or ""
                if "references" in fields:
                    changes["references"] = request.references or ()
                updated = self.accounts.put(
                    account.model_copy(update=changes)
                )
        return DelegatedToolResult(
            disposition="succeeded",
            actual_cost_units=0,
            output={
                "connection_id": updated.connection_id,
                "username": updated.username,
                "country": updated.country,
                "concept": updated.concept,
                "tone": updated.tone,
                "references": [item.model_dump(mode="json") for item in updated.references],
                "status": updated.status,
                "expires_at": updated.expires_at.isoformat(),
            },
        )


def descriptor(*, now: datetime) -> ToolDescriptor:
    template = github_issue_descriptor(
        installation_id="configured:threads", observed_at=now, ready=True
    )
    schema = _JSON.validate_python(_COMMAND.json_schema())
    return template.model_copy(
        update={
            "capability_id": CAPABILITY,
            "owner": "ads_booster.tools.threads_accounts",
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "effect_class": EffectClass.CONTROL_PLANE_WRITE,
            "cost": ToolCost(worst_case_units=0, unit="threads_account_profile"),
        }
    )


__all__ = ["CAPABILITY", "ThreadsAccountProfileTool", "descriptor"]

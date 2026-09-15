from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from pydantic import Field, TypeAdapter

from ads_booster.contracts.agent_run import ToolInvocation, contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.contracts.tool_capability import EffectClass, ToolCost, ToolDescriptor
from ads_booster.threads.oauth import ThreadsOAuthService
from ads_booster.tools.compatibility import DelegatedToolResult
from ads_booster.tools.descriptors import github_issue_descriptor
from ads_booster.tools.threads_tools import ThreadsActorResolver
from ads_booster.transport.json_types import JsonObject

CAPABILITY = "threads.connect"
DEFAULT_SCOPES = (
    "threads_basic",
    "threads_content_publish",
    "threads_read_replies",
    "threads_manage_replies",
    "threads_manage_insights",
    "threads_keyword_search",
)


class ThreadsConnectInput(ContractModel):
    scopes: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)] = DEFAULT_SCOPES


_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class ThreadsConnectTool:
    oauth: ThreadsOAuthService
    actors: ThreadsActorResolver

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
            raise ValueError("threads_connect_descriptor_mismatch")
        actor = self.actors(invocation.tenant_id, invocation.run_id)
        if actor.scheduled:
            raise ValueError("threads_connect_requires_foreground")
        request = ThreadsConnectInput.model_validate(invocation.input)
        link = self.oauth.start(
            workspace_id=actor.workspace_id,
            member_id=actor.member_id,
            scopes=request.scopes,
            now=datetime.now(UTC),
        )
        return DelegatedToolResult(
            disposition="succeeded",
            actual_cost_units=0,
            output={
                "authorization_url": link.authorization_url,
                "expires_at": link.expires_at.isoformat(),
            },
        )


def descriptor(*, now: datetime) -> ToolDescriptor:
    template = github_issue_descriptor(
        installation_id="configured:threads", observed_at=now, ready=True
    )
    schema = _JSON.validate_python(ThreadsConnectInput.model_json_schema())
    return template.model_copy(
        update={
            "capability_id": CAPABILITY,
            "owner": "ads_booster.tools.threads_connection",
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "effect_class": EffectClass.CONTROL_PLANE_WRITE,
            "cost": ToolCost(worst_case_units=0, unit="threads_connection"),
        }
    )


__all__ = ["CAPABILITY", "DEFAULT_SCOPES", "ThreadsConnectTool", "descriptor"]

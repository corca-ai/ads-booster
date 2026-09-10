"""Canonical read-only adapter for marketing funnel decisions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.learning.funnel_analysis import FunnelAnalysisInput, analyze_funnels
from ads_booster.tools.compatibility import DelegatedToolResult
from ads_booster.tools.descriptors import research_descriptor
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor

CAPABILITY = "marketing.analyze"
_JSON: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def execute(invocation: ToolInvocation, descriptor: ToolDescriptor) -> DelegatedToolResult:
    """Use only supplied bounded data; no files, credentials, network or effects."""
    if descriptor.capability_id != CAPABILITY:
        message = "marketing_analysis_descriptor_mismatch"
        raise ValueError(message)
    try:
        request = FunnelAnalysisInput.model_validate(invocation.input)
    except ValidationError as error:
        # Known input rejection has no uncertain side effect to reconcile.
        return DelegatedToolResult(
            disposition="failed",
            actual_cost_units=0,
            output={
                "status": "invalid_input",
                "errors": [
                    {"code": item["type"], "location": [str(part) for part in item["loc"]]}
                    for item in error.errors(include_input=False, include_context=False)
                ],
                "next_action": "Clarify cohorts or correct supplied counts; do not invent data.",
            },
        )
    return DelegatedToolResult(
        disposition="no_effect", actual_cost_units=0, output=analyze_funnels(request)
    )


def descriptor(*, now: datetime) -> ToolDescriptor:
    template = research_descriptor(
        installation_id="installed:marketing.analyze", observed_at=now, ready=True
    )
    schema = _JSON.validate_python(FunnelAnalysisInput.model_json_schema())
    return template.model_copy(
        update={
            "capability_id": CAPABILITY,
            "owner": "ads_booster.tools.marketing_analysis",
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "cost": template.cost.model_copy(update={"worst_case_units": 0, "unit": "calculation"}),
            "credential_boundary": "none",
        }
    )

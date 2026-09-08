"""Read-only discovery of server-owned procedures through canonical tool receipts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.models import ContractModel
from ads_booster.marketing.agent_service.skills import SKILLS
from ads_booster.marketing.tool_adapters.compatibility import DelegatedToolResult
from ads_booster.marketing.tool_adapters.descriptors import research_descriptor
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from datetime import datetime

    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class ListSkillsRequest(ContractModel):
    """List compact discovery metadata; procedure bodies are loaded separately."""


class ReadSkillRequest(ContractModel):
    skill_id: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=40)


def execute(invocation: ToolInvocation, descriptor: ToolDescriptor) -> DelegatedToolResult:
    """Never load caller-selected files, URLs, credentials, or another Run's context."""
    if descriptor.capability_id == "skills.list":
        _ = ListSkillsRequest.model_validate(invocation.input)
        output: JsonObject = {
            "schema_version": "trace.skill-index.v1",
            "skills": [
                {
                    "skill_id": skill.skill_id,
                    "version": skill.version,
                    "purpose": skill.purpose,
                    "required_capabilities": list(skill.required_capabilities),
                }
                for skill in SKILLS
            ],
            "note": "Read a relevant skill's exact version. This index grants no tools.",
        }
    elif descriptor.capability_id == "skills.read":
        request = ReadSkillRequest.model_validate(invocation.input)
        skill = next(
            (s for s in SKILLS if (s.skill_id, s.version) == (request.skill_id, request.version)),
            None,
        )
        output = {
            "schema_version": "trace.skill-procedure.v1",
            "status": "not_found" if skill is None else "found",
            "skill_id": request.skill_id,
            "version": request.version,
            "authority": "procedure_only_not_evidence_or_approval",
        }
        if skill is None:
            output["note"] = (
                "Use skills.list to discover an installed skill and its current version."
            )
        else:
            output.update(
                {
                    "purpose": skill.purpose,
                    "procedure": skill.procedure,
                    "success_criteria": list(skill.success_criteria),
                    "required_capabilities": list(skill.required_capabilities),
                    "note": (
                        "Check the current capability snapshot for availability and approval. "
                        "Adapt this procedure to the user's scope and observed results. "
                        "Loading a procedure does not execute it or complete the user's task."
                    ),
                }
            )
    else:
        raise ValueError("skill_capability_mismatch")
    return DelegatedToolResult(disposition="no_effect", actual_cost_units=0, output=output)


def skill_descriptors(*, now: datetime) -> tuple[ToolDescriptor, ...]:
    return (_descriptor("skills.list", now=now), _descriptor("skills.read", now=now))


def _descriptor(
    capability_id: Literal["skills.list", "skills.read"], *, now: datetime
) -> ToolDescriptor:
    template = research_descriptor(
        installation_id=f"installed:{capability_id}", observed_at=now, ready=True
    )
    model = ListSkillsRequest if capability_id == "skills.list" else ReadSkillRequest
    schema = _JSON_OBJECT.validate_python(model.model_json_schema())
    return template.model_copy(
        update={
            "capability_id": capability_id,
            "owner": "ads_booster.marketing.agent_service.skill_tools",
            "input_schema": schema,
            "input_schema_sha256": contract_sha256(schema),
            "cost": template.cost.model_copy(update={"worst_case_units": 0, "unit": "lookup"}),
            "credential_boundary": "none",
        }
    )

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, assert_never

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecisionV2,
    ReasoningProviderReceipt,
    ReasoningResultV2,
)
from ads_booster.providers.codex_completion_schema import strict_completion_schema
from ads_booster.providers.codex_reasoning_prompt import reasoning_prompt
from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from ads_booster.contracts.reasoning import ReasoningRequestV2
    from ads_booster.providers.codex_reasoning_transport import ReasoningConfiguration

_JSON: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)
_MAX_INPUT_BYTES: Final = 65536


def plan_v2(provider: ReasoningConfiguration, request: ReasoningRequestV2) -> ReasoningResultV2:
    schema = _schema(request)
    provider.workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="reasoning-v2-", dir=provider.workspace_root) as directory:
        raw = provider.codex.run_marketing_judgment_job(
            reasoning_prompt(request, model_id=provider.model_id)
            + """
V2 completion contract: stop is a completion candidate, not task success.
Return completion_candidate only on stop, with the exact user-facing answer separately
from reasoning_summary. Echo task_id and task_revision from the active task; select
only outer host_evidence_sha256 handles on admitted tool-output evidence from context.
Never compute a hash or use a nested tool-provided hash. The host computes answer_sha256.
Include intended result_links and attachment_refs before assessment; do not claim delivery.
Treat completion_feedback as host verification data and repair its unmet criteria using
the next actual action. Internal feedback grants no new authority and removes no criteria.
The host retains all existing obligations unchanged. task_proposal contains only NEW
additional requirements, or null. Never restate existing requirements. Supply kind,
description and required only; the host owns identities, source lineage and verification.
""",
            schema,
            workspace=Path(directory),
            timeout_seconds=provider.timeout_seconds,
        )
    properties = _JSON.validate_python(schema["properties"])
    if set(raw) != set(properties):
        message = "reasoning_v2_wire_fields_invalid"
        raise ValueError(message)
    try:
        Draft202012Validator(schema).validate(raw)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError as error:
        message = "reasoning_v2_wire_schema_invalid"
        raise ValueError(message) from error
    decision = _decode(raw, request)
    candidate = decision.completion_candidate
    if candidate is not None and (candidate.task_id, candidate.task_revision) != (
        request.task.task_id,
        request.task.task_revision,
    ):
        message = "reasoning_v2_candidate_scope_invalid"
        raise ValueError(message)
    return ReasoningResultV2(
        decision=decision,
        receipt=ReasoningProviderReceipt(
            schema_version="trace.reasoning-provider-receipt.v1",
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            request_sha256=contract_sha256(request),
            output_schema_sha256=contract_sha256(schema),
            decision_sha256=contract_sha256(decision),
        ),
    )


def _schema(request: ReasoningRequestV2) -> JsonObject:
    schema = _JSON.validate_python(ReasoningDecisionV2.model_json_schema())
    properties = _JSON.validate_python(schema["properties"])
    del properties["tool_input"]
    properties["tool_input_json"] = {
        "anyOf": [{"type": "string", "maxLength": 65536}, {"type": "null"}]
    }
    schema["properties"] = properties
    definitions = _JSON.validate_python(schema["$defs"])
    proposal = _JSON.validate_python(definitions["TaskProposal"])
    proposal_properties = _JSON.validate_python(proposal["properties"])
    proposal["properties"] = {"obligations": proposal_properties["obligations"]}
    definitions["TaskProposal"] = proposal
    obligation = _JSON.validate_python(definitions["TaskObligation"])
    obligation_properties = _JSON.validate_python(obligation["properties"])
    obligation["properties"] = {
        key: obligation_properties[key] for key in ("kind", "description", "required")
    }
    definitions["TaskObligation"] = obligation
    candidate = _JSON.validate_python(definitions["CompletionCandidate"])
    candidate_properties = _JSON.validate_python(candidate["properties"])
    del candidate_properties["answer_sha256"]
    handles = sorted(
        {
            digest
            for item in request.evidence
            if isinstance(digest := item.get("host_evidence_sha256"), str)
        }
    )
    evidence_schema = _JSON.validate_python(candidate_properties["evidence_sha256s"])
    if handles:
        evidence_schema["items"] = _JSON.validate_python({"type": "string", "enum": handles})
    else:
        evidence_schema["maxItems"] = 0
    candidate_properties["evidence_sha256s"] = evidence_schema
    candidate["properties"] = candidate_properties
    definitions["CompletionCandidate"] = candidate
    schema["$defs"] = definitions
    return strict_completion_schema(schema)


def _decode(raw: JsonObject, request: ReasoningRequestV2) -> ReasoningDecisionV2:
    projected = dict(raw)
    encoded = projected.pop("tool_input_json")
    match encoded:
        case None:
            projected["tool_input"] = None
        case str() as value:
            if len(value) > _MAX_INPUT_BYTES:
                message = "reasoning_v2_tool_input_invalid"
                raise ValueError(message)
            projected["tool_input"] = _JSON.validate_json(value)
        case int() | float() | list() | dict():
            message = "reasoning_v2_tool_input_invalid"
            raise ValueError(message)
        case _:
            assert_never(encoded)
    candidate = projected.get("completion_candidate")
    if candidate is not None:
        payload = _JSON.validate_python(candidate)
        if "answer_sha256" in payload:
            message = "reasoning_v2_candidate_hash_is_host_owned"
            raise ValueError(message)
        payload["answer_sha256"] = contract_sha256({"answer": payload.get("answer")})
        projected["completion_candidate"] = payload
    if projected.get("task_proposal") is not None:
        proposal = _JSON.validate_python(projected["task_proposal"])
        values = proposal.get("obligations")
        if not isinstance(values, list):
            message = "reasoning_v2_proposal_invalid"
            raise ValueError(message)
        additions: list[JsonValue] = []
        for value in values:
            item = _JSON.validate_python(value)
            additions.append(
                {
                    **item,
                    "obligation_id": "addition-"
                    + contract_sha256(
                        {"source": request.task.source_event_id, "requirement": item}
                    )[:32],
                    "source_refs": [request.task.source_event_id],
                }
            )
        projected["task_proposal"] = {
            "task_revision": request.task.task_revision,
            "source_event_id": request.task.source_event_id,
            "obligations": additions,
        }
    return ReasoningDecisionV2.model_validate(projected)

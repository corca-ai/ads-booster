from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, assert_never

from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecisionV2,
    ReasoningProviderReceipt,
    ReasoningResultV2,
)
from ads_booster.providers.codex_completion_schema import strict_completion_schema
from ads_booster.providers.codex_reasoning_prompt import reasoning_prompt
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from ads_booster.contracts.reasoning import ReasoningRequestV2
    from ads_booster.providers.codex_reasoning_transport import ReasoningConfiguration

_JSON: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)
_MAX_INPUT_BYTES: Final = 65536


def plan_v2(provider: ReasoningConfiguration, request: ReasoningRequestV2) -> ReasoningResultV2:
    schema = _schema()
    provider.workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="reasoning-v2-", dir=provider.workspace_root) as directory:
        raw = provider.codex.run_marketing_judgment_job(
            reasoning_prompt(request)
            + """
V2 completion contract: stop is a completion candidate, not task success.
Return completion_candidate only on stop, with the exact user-facing answer separately
from reasoning_summary. Echo task_id and task_revision from the active task; select
only outer host_evidence_sha256 handles on admitted tool-output evidence from context.
Never compute a hash or use a nested tool-provided hash. The host computes answer_sha256.
Include intended result_links and attachment_refs before assessment; do not claim delivery.
Treat completion_feedback as host verification data and repair its unmet criteria using
the next actual action. Internal feedback grants no new authority and removes no criteria.
Task proposals must retain every required obligation and its host source_refs.
""",
            schema,
            workspace=Path(directory),
            timeout_seconds=provider.timeout_seconds,
        )
    properties = _JSON.validate_python(schema["properties"])
    if set(raw) != set(properties):
        message = "reasoning_v2_wire_fields_invalid"
        raise ValueError(message)
    decision = _decode(raw)
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


def _schema() -> JsonObject:
    schema = _JSON.validate_python(ReasoningDecisionV2.model_json_schema())
    properties = _JSON.validate_python(schema["properties"])
    del properties["tool_input"]
    properties["tool_input_json"] = {
        "anyOf": [{"type": "string", "maxLength": 65536}, {"type": "null"}]
    }
    schema["properties"] = properties
    definitions = _JSON.validate_python(schema["$defs"])
    candidate = _JSON.validate_python(definitions["CompletionCandidate"])
    candidate_properties = _JSON.validate_python(candidate["properties"])
    del candidate_properties["answer_sha256"]
    candidate["properties"] = candidate_properties
    definitions["CompletionCandidate"] = candidate
    schema["$defs"] = definitions
    return strict_completion_schema(schema)


def _decode(raw: JsonObject) -> ReasoningDecisionV2:
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
    return ReasoningDecisionV2.model_validate(projected)

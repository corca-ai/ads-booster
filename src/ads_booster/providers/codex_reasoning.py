"""Official Codex CLI adapter for the replaceable ReasoningProvider port."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningRequestV2,
    ReasoningResult,
    ReasoningResultV2,
)
from ads_booster.execution_control import ExecutionCancelledError
from ads_booster.providers.codex_reasoning_prompt import reasoning_prompt as _prompt
from ads_booster.providers.codex_reasoning_transport import StructuredReasoningRunner
from ads_booster.providers.codex_reasoning_v2 import plan_v2
from ads_booster.transport.json_types import JsonObject, JsonValue

_MAX_TOOL_INPUT_BYTES = 65536
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class CodexReasoningError(RuntimeError):
    """A sanitized provider failure that cannot mutate Agent state."""


@dataclass(frozen=True, slots=True)
class CodexReasoningProvider:
    codex: StructuredReasoningRunner
    workspace_root: Path
    model_id: str
    timeout_seconds: float = 300.0
    provider_id: str = "official-codex-cli"

    def plan_v2(self, request: ReasoningRequestV2) -> ReasoningResultV2:
        try:
            return plan_v2(self, request)
        except ExecutionCancelledError:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            message = "reasoning_provider_result_invalid"
            raise CodexReasoningError(message) from error

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        request_sha256 = contract_sha256(request)
        schema = _wire_schema()
        self.workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(
                prefix=f"reasoning-{request_sha256[:16]}-",
                dir=self.workspace_root,
            ) as directory:
                raw = self.codex.run_marketing_judgment_job(
                    _prompt(request, model_id=self.model_id),
                    schema,
                    workspace=Path(directory),
                    timeout_seconds=self.timeout_seconds,
                )
            decision = _decode_decision(raw)
        except ExecutionCancelledError:
            raise
        except (OSError, RuntimeError, ValidationError, ValueError) as error:
            message = "reasoning_provider_result_invalid"
            raise CodexReasoningError(message) from error
        return ReasoningResult(
            schema_version="trace.reasoning-result.v1",
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id=self.provider_id,
                model_id=self.model_id,
                request_sha256=request_sha256,
                output_schema_sha256=contract_sha256(schema),
                decision_sha256=contract_sha256(decision),
            ),
        )


def _decode_decision(raw: JsonObject) -> ReasoningDecision:
    encoded_input = raw.get("tool_input_json")
    if (
        "tool_input_json" not in raw
        or (encoded_input is not None and not isinstance(encoded_input, str))
        or (isinstance(encoded_input, str) and len(encoded_input) > _MAX_TOOL_INPUT_BYTES)
    ):
        message = "reasoning_wire_input_invalid"
        raise ValueError(message)
    projected = dict(raw)
    del projected["tool_input_json"]
    projected["tool_input"] = (
        None if encoded_input is None else _JSON_OBJECT.validate_json(encoded_input)
    )
    return ReasoningDecision.model_validate(projected)


def _wire_schema() -> JsonObject:
    # Strict structured output cannot represent arbitrary recursive JSON maps.
    # Encode only the tool input at the provider boundary; the canonical service
    # still validates the decoded object against the chosen ToolDescriptor.
    schema = _JSON_OBJECT.validate_python(ReasoningDecision.model_json_schema())
    properties = schema["properties"]
    if not isinstance(properties, dict):
        message = "reasoning_schema_invalid"
        raise TypeError(message)
    del properties["tool_input"]
    properties["tool_input_json"] = {
        "anyOf": [{"type": "string", "maxLength": 65536}, {"type": "null"}],
        "description": "JSON-encoded tool input object, or null when not invoking",
    }
    schema["required"] = list(properties)
    return _inline_schema_definitions(schema)


def _inline_schema_definitions(schema: JsonObject) -> JsonObject:
    definitions = schema.pop("$defs", None)
    if definitions is None:
        return schema
    if not isinstance(definitions, dict):
        message = "reasoning_schema_definitions_invalid"
        raise TypeError(message)

    def expand(value: JsonValue, resolving: frozenset[str]) -> JsonValue:
        if isinstance(value, list):
            return [expand(item, resolving) for item in value]
        if not isinstance(value, dict):
            return value

        reference = value.get("$ref")
        if reference is None:
            return {key: expand(item, resolving) for key, item in value.items()}
        if (
            not isinstance(reference, str)
            or not reference.startswith("#/$defs/")
            or len(value) != 1
        ):
            message = "reasoning_schema_reference_invalid"
            raise TypeError(message)

        name = reference.removeprefix("#/$defs/")
        definition = definitions.get(name)
        if not isinstance(definition, dict) or name in resolving:
            message = "reasoning_schema_reference_unresolved"
            raise TypeError(message)
        return expand(definition, resolving | {name})

    expanded = expand(schema, frozenset())
    if not isinstance(expanded, dict):
        message = "reasoning_schema_invalid"
        raise TypeError(message)
    return expanded


__all__ = ["CodexReasoningError", "CodexReasoningProvider", "StructuredReasoningRunner"]

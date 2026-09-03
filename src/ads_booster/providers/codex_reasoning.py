"""Official Codex CLI adapter for the replaceable ReasoningProvider port."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecision,
    ReasoningProviderReceipt,
    ReasoningRequest,
    ReasoningResult,
)
from ads_booster.transport.json_types import JsonObject

_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class StructuredReasoningRunner(Protocol):
    def run_marketing_judgment_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject: ...


class CodexReasoningError(RuntimeError):
    """A sanitized provider failure that cannot mutate Agent state."""


@dataclass(frozen=True, slots=True)
class CodexReasoningProvider:
    codex: StructuredReasoningRunner
    workspace_root: Path
    model_id: str
    timeout_seconds: float = 300.0
    provider_id: str = "official-codex-cli"

    def plan(self, request: ReasoningRequest) -> ReasoningResult:
        request_sha256 = contract_sha256(request)
        schema = _JSON_OBJECT.validate_python(ReasoningDecision.model_json_schema())
        self.workspace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(
                prefix=f"reasoning-{request_sha256[:16]}-",
                dir=self.workspace_root,
            ) as directory:
                raw = self.codex.run_marketing_judgment_job(
                    _prompt(request),
                    schema,
                    workspace=Path(directory),
                    timeout_seconds=self.timeout_seconds,
                )
            decision = ReasoningDecision.model_validate(raw)
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


def _prompt(request: ReasoningRequest) -> str:
    return f"""You are the replaceable reasoning provider for one Marketing Agent step.
Choose only a capability_id present in capability_snapshot.descriptors.
Unavailable tools are absent and must not be requested.
You may instead request_input or stop. Do not claim that any tool ran.
Return every schema field; use null for capability_id and tool_input when not invoking.
Canonical request:
{request.model_dump_json()}"""


__all__ = ["CodexReasoningError", "CodexReasoningProvider", "StructuredReasoningRunner"]

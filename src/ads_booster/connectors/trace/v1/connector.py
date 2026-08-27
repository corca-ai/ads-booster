from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ads_booster.agent.runs import (
    AgentGoal,
    AgentRun,
    CompletionDecision,
    CompletionDisposition,
    ConnectorId,
    ConnectorManifest,
    ObservationKind,
)
from ads_booster.connectors.trace.v1.references import (
    TraceReferenceError,
    reference_context_messages,
)
from ads_booster.connectors.trace.v1.tools import (
    TraceGenerateMarketingImageTool,
    TracePlannedImageRunner,
)
from ads_booster.contracts.results import TraceRunResult
from ads_booster.contracts.run import TraceRunState

_REFERENCE_ROOT_MISSING = "trace_reference_root_missing"
_ALL_REFERENCES = "all"

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.generation import MarketingContextBundle
    from ads_booster.tools.models import Tool
    from ads_booster.transport.json_types import JsonObject


def trace_connector_manifest() -> ConnectorManifest:
    return ConnectorManifest(
        connector_id=ConnectorId("trace-marketing"),
        version="1.0.0",
        description="Trace native marketing image connector",
    )


@dataclass(frozen=True, slots=True)
class TraceMarketingConnector:
    bundle: MarketingContextBundle
    runner: TracePlannedImageRunner
    reference_root: Path | None = None
    manifest: ConnectorManifest = field(default_factory=trace_connector_manifest)

    def instructions(self, goal: AgentGoal) -> str:
        del goal
        return (
            "Create a complete scene plan, call trace_generate_marketing_image, inspect its typed "
            "result, and continue planning until a request-bound native artifact is ready for "
            "human review. Never invent native provenance or omit supplied references."
        )

    def context_messages(self, goal: AgentGoal) -> tuple[JsonObject, ...]:
        del goal
        if not self.bundle.reference_images:
            return ()
        if self.reference_root is None:
            raise TraceReferenceError(_REFERENCE_ROOT_MISSING, _ALL_REFERENCES)
        return reference_context_messages(self.reference_root, self.bundle.reference_images)

    def tools(self, goal: AgentGoal) -> tuple[Tool, ...]:
        del goal
        return (TraceGenerateMarketingImageTool(self.bundle, self.runner),)

    def validate_completion(self, run: AgentRun, answer: str) -> CompletionDecision:
        del answer
        result = self.completed_result(run)
        if result is None:
            return CompletionDecision(
                disposition=CompletionDisposition.CONTINUE,
                message=(
                    "No verified native Trace artifact exists yet. Revise the scene plan and call "
                    "trace_generate_marketing_image."
                ),
            )
        return CompletionDecision(
            disposition=CompletionDisposition.AWAITING_APPROVAL,
            message="native Trace artifact awaits human review",
            data={
                "output_image": result.output_image,
                "output_image_sha256": result.output_image_sha256,
                "component_artifact": result.component_artifact,
                "component_artifact_sha256": result.component_artifact_sha256,
            },
        )

    def completed_result(self, run: AgentRun) -> TraceRunResult | None:
        """Return the latest native artifact accepted by the connector contract."""
        return _latest_completed_result(run)


def _latest_completed_result(run: AgentRun) -> TraceRunResult | None:
    cutoff = 0
    for observation in reversed(run.observations):
        if observation.kind is not ObservationKind.APPROVAL:
            continue
        accepted = observation.data.get("accepted")
        history_length = observation.data.get("history_length")
        if accepted is False and isinstance(history_length, int):
            cutoff = history_length
            break
    for item in reversed(run.history[cutoff:]):
        if item.get("type") != "function_call_output":
            continue
        output = item.get("output")
        if not isinstance(output, str):
            continue
        try:
            result = TraceRunResult.model_validate_json(output)
        except ValidationError:
            continue
        provenance = result.capture_provenance
        if (
            result.state is TraceRunState.COMPLETED
            and provenance is not None
            and provenance.source == "native_appium"
            and provenance.native_export_binding_verified
        ):
            return result
    return None

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, ClassVar

from pydantic import Field, TypeAdapter, ValidationError

from ads_booster.agent.runs import (
    AgentGoal,
    AgentRun,
    CompletionDecision,
    CompletionDisposition,
    ConnectorManifest,
)
from ads_booster.candidate_generation.models import CandidateDraft, GenerationModel
from ads_booster.connectors.trace.v1.connector import trace_connector_manifest
from ads_booster.contracts.tools import ToolDescriptor
from ads_booster.tools.models import ToolContext, ToolResult
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from ads_booster.candidate_generation.models import CandidateContextBundle
    from ads_booster.tools.models import Tool

_DRAFTS: TypeAdapter[tuple[CandidateDraft, ...]] = TypeAdapter(tuple[CandidateDraft, ...])
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


class TraceCandidateBatchArgs(GenerationModel):
    candidates: Annotated[tuple[CandidateDraft, ...], Field(min_length=1, max_length=8)]


class TraceProposeCandidatesTool:
    name: ClassVar[str] = "trace_propose_marketing_candidates"

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            name=self.name,
            description=(
                "Propose a complete batch of model-authored Trace marketing candidates. Supply "
                "every candidate field through the typed schema."
            ),
            parameters=TraceCandidateBatchArgs.model_json_schema(),
            strict=True,
        )

    def execute(self, arguments: JsonObject, context: ToolContext) -> ToolResult:
        del context
        try:
            parsed = TraceCandidateBatchArgs.model_validate(arguments)
        except ValidationError as error:
            return ToolResult(ok=False, output=str(error), error_code="candidate_batch_invalid")
        topics = tuple(candidate.topic for candidate in parsed.candidates)
        if len(set(topics)) != len(topics):
            return ToolResult(
                ok=False,
                output="candidate topics must be distinct",
                error_code="candidate_topics_duplicated",
            )
        return ToolResult(ok=True, output=_DRAFTS.dump_json(parsed.candidates).decode())


@dataclass(frozen=True, slots=True)
class TraceCandidateConnector:
    context: CandidateContextBundle
    manifest: ConnectorManifest = field(default_factory=trace_connector_manifest)

    def instructions(self, goal: AgentGoal) -> str:
        del goal
        return (
            "Use the supplied context documents to create a useful set of distinct marketing "
            "candidates. Choose country, posting slot, and creative direction from the context. "
            "Call trace_propose_marketing_candidates and "
            "continue until its typed result succeeds."
        )

    def context_messages(self, goal: AgentGoal) -> tuple[JsonObject, ...]:
        del goal
        return tuple(
            _JSON_OBJECT.validate_python(
                {
                    "role": "developer",
                    "content": json.dumps(
                        {
                            "relative_path": document.relative_path,
                            "text": document.text,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            )
            for document in self.context.documents
        )

    def tools(self, goal: AgentGoal) -> tuple[Tool, ...]:
        del goal
        return (TraceProposeCandidatesTool(),)

    def validate_completion(self, run: AgentRun, answer: str) -> CompletionDecision:
        drafts = self.completed_drafts(run)
        if drafts:
            return CompletionDecision(
                disposition=CompletionDisposition.COMPLETED,
                message=answer or "candidate batch proposed",
                data={"candidate_count": len(drafts)},
            )
        return CompletionDecision(
            disposition=CompletionDisposition.CONTINUE,
            message=(
                "No valid candidate batch was proposed. Correct the tool arguments and call "
                "trace_propose_marketing_candidates again."
            ),
        )

    def completed_drafts(self, run: AgentRun) -> tuple[CandidateDraft, ...]:
        for item in reversed(run.history):
            if item.get("type") != "function_call_output":
                continue
            output = item.get("output")
            if not isinstance(output, str):
                continue
            try:
                drafts = _DRAFTS.validate_json(output)
            except ValidationError:
                continue
            if drafts:
                return drafts
        return ()

from __future__ import annotations

import io
from hashlib import sha256
from typing import TYPE_CHECKING, Literal

from PIL import Image
from pydantic import TypeAdapter

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.reasoning import (
    ReasoningDecisionV2,
    ReasoningProviderReceipt,
    ReasoningRequestV2,
    ReasoningResultV2,
)
from ads_booster.contracts.task_completion import (
    CompletionCandidate,
    ObligationAssessment,
    SemanticAssessmentRequest,
    SemanticAssessmentResult,
)
from ads_booster.contracts.task_progress import TaskObligation, TaskProposal
from ads_booster.contracts.tool_capability import ToolExecutionResult

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.contracts.agent_run import ToolInvocation
    from ads_booster.contracts.tool_capability import ToolDescriptor

_EVIDENCE_HANDLE = TypeAdapter(str)


class FixtureImageTool:
    def __init__(
        self,
        root: Path,
        disposition: Literal["succeeded", "no_effect"] = "succeeded",
        *,
        colors: tuple[str, ...] = ("blue",),
    ) -> None:
        self.root: Path = root
        self.calls: int = 0
        self.disposition: Literal["succeeded", "no_effect"] = disposition
        self.colors: tuple[str, ...] = colors

    def execute(
        self, invocation: ToolInvocation, descriptor: ToolDescriptor
    ) -> ToolExecutionResult:
        _ = descriptor
        self.calls += 1
        if self.disposition == "no_effect":
            return ToolExecutionResult(
                schema_version="trace.tool-execution-result.v1",
                disposition="no_effect",
                invocation_sha256=contract_sha256(invocation),
                executor_id="codex.image_generation",
                actual_cost_units=1,
                output={"status": "prepared_not_executed"},
            )
        self.root.mkdir(parents=True, exist_ok=True)
        stream = io.BytesIO()
        color = self.colors[(self.calls - 1) % len(self.colors)]
        Image.new("RGB", (128, 128), color).save(stream, format="PNG")
        data = stream.getvalue()
        digest = sha256(data).hexdigest()
        _ = (self.root / f"{digest}.png").write_bytes(data)
        return ToolExecutionResult(
            schema_version="trace.tool-execution-result.v1",
            disposition="succeeded",
            invocation_sha256=contract_sha256(invocation),
            executor_id="codex.image_generation",
            actual_cost_units=1,
            output={
                "artifact_sha256": digest,
                "width": 128,
                "height": 128,
                "media_type": "image/png",
                "invocation_sha256": contract_sha256(invocation),
            },
        )


class RepairingImagePlanner:
    def __init__(self, prompt: str = "Blue square") -> None:
        self.requests: list[ReasoningRequestV2] = []
        self.prompt: str = prompt

    def plan_v2(self, request: ReasoningRequestV2) -> ReasoningResultV2:
        self.requests.append(request)
        outputs = tuple(
            item
            for item in request.evidence
            if item.get("schema_version") == "trace.tool-output-evidence.v1"
        )
        proposal = None
        if not any(item.kind == "artifact" for item in request.task.obligations):
            proposal = TaskProposal(
                task_revision=request.task.task_revision,
                source_event_id=request.task.source_event_id,
                obligations=(
                    *request.task.obligations,
                    TaskObligation(
                        obligation_id="image-bytes",
                        kind="artifact",
                        description="Readable PNG image",
                        source_refs=(request.task.source_event_id,),
                    ),
                ),
            )
        invoke = request.completion_feedback is not None and not outputs
        answer = "The image artifact is ready."
        candidate = (
            None
            if invoke
            else CompletionCandidate(
                candidate_id=f"candidate-{len(self.requests)}",
                task_id=request.task.task_id,
                task_revision=request.task.task_revision,
                answer=answer,
                answer_sha256=contract_sha256({"answer": answer}),
                evidence_sha256s=tuple(
                    _EVIDENCE_HANDLE.validate_python(item["host_evidence_sha256"])
                    for item in outputs
                ),
            )
        )
        decision = ReasoningDecisionV2(
            action="invoke_tool" if invoke else "stop",
            capability_id="creative.image.generate" if invoke else None,
            tool_input={"prompt": self.prompt} if invoke else None,
            expected_outcome="Readable PNG image",
            reasoning_summary="Execute the missing creation",
            task_proposal=proposal,
            completion_candidate=candidate,
        )
        return ReasoningResultV2(
            decision=decision,
            receipt=ReasoningProviderReceipt(
                schema_version="trace.reasoning-provider-receipt.v1",
                provider_id="fixture-image",
                model_id="scripted",
                request_sha256=contract_sha256(request),
                decision_sha256=contract_sha256(decision),
                output_schema_sha256="d" * 64,
            ),
        )


class ImageExistenceAssessor:
    def assess(self, request: SemanticAssessmentRequest) -> SemanticAssessmentResult:
        evidence = tuple(
            item.evidence_sha256
            for item in request.evidence
            if item.verified and item.kind == "artifact"
        )
        supported = request.original_objective == "Create a blue PNG image" and bool(evidence)
        return SemanticAssessmentResult(
            request_sha256=contract_sha256(request),
            candidate_sha256=contract_sha256(request.candidate),
            requested_deliverables_supported=supported,
            uncovered_requirements=() if supported else ("Readable PNG image",),
            obligations=tuple(
                ObligationAssessment(
                    obligation_id=item.obligation_id,
                    status="satisfied" if supported else "unsatisfied",
                    mechanism="fixture_image_existence",
                    reason="Readable bytes" if supported else "Image missing",
                    evidence_sha256s=evidence,
                )
                for item in request.obligations
            ),
        )

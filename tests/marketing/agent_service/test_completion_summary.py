from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from pydantic import TypeAdapter

from ads_booster.agent.service.completion_evidence import BoundCompletionEvidence
from ads_booster.contracts.agent_run import ToolInvocation, ToolReceiptRecord, contract_sha256
from ads_booster.creative.managed_image_review import managed_image_review_descriptor
from ads_booster.tools.completion_summary import evidence_description
from ads_booster.tools.image_review import VisualAssessment, VisualFinding
from ads_booster.transport.json_types import JsonObject

_JSON: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)


def test_large_visual_review_retains_source_bound_model_findings() -> None:
    now = datetime(2026, 9, 3, tzinfo=UTC)
    descriptor = managed_image_review_descriptor(now=now, ready=True)
    invocation = ToolInvocation(
        schema_version="trace.tool-invocation.v1",
        tenant_id="trace",
        invocation_id="review-one",
        run_id="run-one",
        step_id="step-one",
        intent_sha256="a" * 64,
        capability_snapshot_sha256="b" * 64,
        descriptor_sha256=contract_sha256(descriptor),
        idempotency_key="review-one",
        input={"request": "Inspect the image"},
        input_sha256=contract_sha256({"request": "Inspect the image"}),
    )
    assessment = VisualAssessment(
        schema_version="trace.image-visual-assessment.v1",
        summary="Low contrast",
        findings=tuple(
            VisualFinding(
                source_index=0,
                area="contrast",
                severity="blocker",
                location="Title",
                observation="Too faint " * 50,
                recommendation="Increase contrast",
            )
            for _ in range(24)
        ),
        uncertainties=(),
        human_questions=(),
    )
    output: JsonObject = {
        "review": {
            "model_visual_assessment": assessment.model_dump(mode="json"),
            "receipt": {
                "source_sha256s": ["c" * 64],
                "assessment_sha256": contract_sha256(assessment),
            },
            "human_review": {"status": "required", "final_approval": False},
        }
    }
    receipt = ToolReceiptRecord(
        schema_version="trace.tool-receipt.v1",
        receipt_id="receipt-one",
        invocation_sha256=contract_sha256(invocation),
        disposition="no_effect",
        actual_cost_units=4,
        output_schema_sha256=contract_sha256(descriptor.output_schema),
        output_sha256=contract_sha256(output),
        executor_id="managed-image-review",
        occurred_at=now,
    )
    bound = BoundCompletionEvidence(invocation, descriptor, receipt, output, "e" * 64)

    description = evidence_description(bound)

    parsed = _JSON.validate_json(description)
    assert len(description) <= 4000
    assert parsed["projection_truncated"] is True
    assert parsed["canonical_evidence_sha256"] == "e" * 64
    review = parsed["visual_review"]
    assert isinstance(review, dict)
    assert review["method"] == "model_visual"
    assert review["findings_by_severity"] == {"blocker": 24}
    assert review["finding_details_omitted"] == 24
    assert review["human_review"] == {"status": "required", "final_approval": False}

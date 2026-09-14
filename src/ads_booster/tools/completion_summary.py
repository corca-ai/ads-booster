from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Final

from ads_booster.tools.image_review import VisualAssessment

if TYPE_CHECKING:
    from ads_booster.agent.service.completion_evidence import BoundCompletionEvidence
    from ads_booster.transport.json_types import JsonObject

_MAX_DESCRIPTION: Final = 4000


def evidence_description(bound: BoundCompletionEvidence) -> str:
    base: JsonObject = {
        "capability_id": bound.descriptor.capability_id,
        "disposition": bound.receipt.disposition,
        "canonical_evidence_sha256": bound.evidence_sha256,
    }
    full = json.dumps(
        {**base, "input": bound.invocation.input, "output": bound.output}, ensure_ascii=False
    )
    if len(full) <= _MAX_DESCRIPTION:
        return full
    projected: JsonObject = {
        **base,
        "projection_truncated": True,
        "input_excerpt": json.dumps(bound.invocation.input, ensure_ascii=False)[:600],
    }
    review = bound.output.get("review", bound.output)
    known_review = (bound.descriptor.capability_id, bound.descriptor.owner) in {
        ("creative.asset.review", "ads_booster.marketing.agent_service.managed_image_review"),
        ("creative.image.review", "slack_authorized_image_review"),
    }
    if known_review and isinstance(review, dict) and "model_visual_assessment" in review:
        assessment = VisualAssessment.model_validate(review["model_visual_assessment"])
        counts = Counter(item.severity for item in assessment.findings)
        projected["visual_review"] = {
            "method": "model_visual",
            "summary_excerpt": assessment.summary[:700],
            "findings_by_severity": dict(counts),
            "finding_details_omitted": len(assessment.findings),
            "uncertainty_count": len(assessment.uncertainties),
            "human_question_count": len(assessment.human_questions),
            "human_review": review.get("human_review"),
            "receipt": review.get("receipt"),
            "quantitative_checks": review.get("quantitative_checks"),
        }
    else:
        projected["output"] = {
            key: value
            for key, value in bound.output.items()
            if key
            in {
                "artifact_sha256",
                "width",
                "height",
                "media_type",
                "review_status",
                "repository",
                "number",
                "url",
                "human_review_required",
                "product_support_verified",
            }
        }
        asset = bound.output.get("asset")
        if isinstance(asset, dict):
            projected["asset"] = {
                key: asset.get(key) for key in ("asset_id", "revision", "sha256", "parents", "qa")
            }
        if not projected["output"] and "asset" not in projected:
            projected["output_excerpt"] = json.dumps(bound.output, ensure_ascii=False)[:1800]
    description = json.dumps(projected, ensure_ascii=False)
    if len(description) > _MAX_DESCRIPTION:
        return json.dumps({**base, "projection_truncated": True, "content_omitted": True})
    return description

"""Golden parity for the Python and hosted-JavaScript next-intent registries."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ads_booster.contracts.marketing_agent import contract_sha256
from ads_booster.marketing.dynamic_evidence_research import (
    DynamicEvidenceFinding,
    DynamicEvidenceResearchResult,
    ResearchContinuation,
)
from ads_booster.marketing.evidence_research_operator import ResearchScope
from ads_booster.marketing.hosted_feature_launch_run import (
    build_feature_launch_intent_snapshot,
    next_intent_input_schema_sha256,
    next_intent_output_schema_sha256,
    next_intent_planner_protocol_sha256,
)
from ads_booster.transport.json_types import JsonObject

if TYPE_CHECKING:
    from ads_booster.contracts.marketing_capability import ResearchCapabilityScope

ROOT = Path(__file__).parents[2]
RESULT_SHA256 = "a" * 64
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class _ParityCase:
    run_id: str
    findings: tuple[DynamicEvidenceFinding, ...]
    has_continuation: bool
    resumable_scopes: tuple[ResearchCapabilityScope, ...]

    def node_payload(self) -> JsonObject:
        return {
            "run_id": self.run_id,
            "research_result_sha256": RESULT_SHA256,
            "research_result": {
                "findings": [item.model_dump(mode="json") for item in self.findings],
            },
            "has_continuation": self.has_continuation,
            "resumable_scopes": list(self.resumable_scopes),
        }


def test_python_and_javascript_intent_snapshots_have_canonical_golden_parity() -> None:
    cases = (
        _case(
            "all-sufficient-충분",
            (_finding(ResearchScope.PRODUCT_TRUTH, "sufficient"),),
            resumable_scopes=("customer_intelligence",),
        ),
        _case(
            "insufficient-순서-🧭",
            (
                _finding(ResearchScope.MARKET_EVIDENCE, "insufficient"),
                _finding(ResearchScope.CUSTOMER_INTELLIGENCE, "insufficient"),
                _finding(ResearchScope.PRODUCT_TRUTH, "insufficient"),
            ),
            resumable_scopes=("market_evidence", "product_truth"),
        ),
        _case(
            "continuation-재개-🚀",
            (
                _finding(ResearchScope.MARKET_EVIDENCE, "insufficient"),
                _finding(ResearchScope.CUSTOMER_INTELLIGENCE, "insufficient"),
                _finding(ResearchScope.PRODUCT_TRUTH, "insufficient"),
            ),
            has_continuation=True,
            resumable_scopes=("market_evidence", "product_truth"),
        ),
    )

    javascript = _derive_with_node(cases)
    derived_cases = javascript["cases"]
    assert isinstance(derived_cases, list)
    for source, raw_derived in zip(cases, derived_cases, strict=True):
        derived = _JSON_OBJECT.validate_python(raw_derived)
        result = DynamicEvidenceResearchResult.model_construct(
            findings=source.findings,
            continuation=(
                ResearchContinuation.model_construct() if source.has_continuation else None
            ),
        )
        snapshot = build_feature_launch_intent_snapshot(
            source.run_id,
            result,
            research_result_sha256=RESULT_SHA256,
            resumable_scopes=source.resumable_scopes,
        )
        snapshot_json = snapshot.model_dump(mode="json")

        assert derived["snapshot"] == snapshot_json
        assert derived["canonical_json"] == _canonical_json(snapshot_json)
        assert derived["sha256"] == contract_sha256(snapshot)

    assert javascript["constants"] == {
        "input_schema_sha256": next_intent_input_schema_sha256(),
        "output_schema_sha256": next_intent_output_schema_sha256(),
        "planner_protocol_sha256": next_intent_planner_protocol_sha256(),
    }


def _finding(
    scope: ResearchScope,
    status: str,
) -> DynamicEvidenceFinding:
    return DynamicEvidenceFinding.model_validate(
        {
            "iteration": 1,
            "scope": scope.value,
            "evidence_status": status,
            "summary": "유니코드 요약 — canonical parity",
            "source_ref": f"fixture:{scope.value}",
            "source_sha256": "b" * 64,
            "trust_state": "packet_bound",
        }
    )


def _case(
    run_id: str,
    findings: tuple[DynamicEvidenceFinding, ...],
    *,
    has_continuation: bool = False,
    resumable_scopes: tuple[ResearchCapabilityScope, ...] = (),
) -> _ParityCase:
    return _ParityCase(run_id, findings, has_continuation, resumable_scopes)


def _derive_with_node(cases: tuple[_ParityCase, ...]) -> JsonObject:
    node_cases = [case.node_payload() for case in cases]
    script = """
import {
  NEXT_INTENT_INPUT_SCHEMA_SHA256,
  NEXT_INTENT_OUTPUT_SCHEMA_SHA256,
  NEXT_INTENT_PLANNER_PROTOCOL_SHA256,
  deriveFeatureLaunchIntentSnapshot,
} from './cloudflare/src/marketing-run-intents.js';
import { canonicalJson } from './cloudflare/src/marketing-run-capabilities.js';

const input = JSON.parse(await new Promise((resolve) => {
  let data = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => { data += chunk; });
  process.stdin.on('end', () => resolve(data));
}));
const cases = [];
for (const item of input) {
  const derived = await deriveFeatureLaunchIntentSnapshot(
    item.run_id,
    item.research_result,
    item.research_result_sha256,
    item.has_continuation,
    item.resumable_scopes,
  );
  cases.push({
    snapshot: derived.snapshot,
    sha256: derived.sha256,
    canonical_json: canonicalJson(derived.snapshot),
  });
}
process.stdout.write(JSON.stringify({
  cases,
  constants: {
    input_schema_sha256: NEXT_INTENT_INPUT_SCHEMA_SHA256,
    output_schema_sha256: NEXT_INTENT_OUTPUT_SCHEMA_SHA256,
    planner_protocol_sha256: NEXT_INTENT_PLANNER_PROTOCOL_SHA256,
  },
}));
"""
    completed = subprocess.run(  # noqa: S603 - fixed local Node executable and script.
        ["node", "--input-type=module", "--eval", script],  # noqa: S607
        cwd=ROOT,
        input=json.dumps(node_cases, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=True,
    )
    return _JSON_OBJECT.validate_json(completed.stdout)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

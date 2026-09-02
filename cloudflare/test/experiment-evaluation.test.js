import assert from "node:assert/strict";
import test from "node:test";

import { deriveExperimentEvaluation } from "../src/experiment-evaluation.js";

function registration() {
  return {
    experiment_id: "experiment-causal-1",
    activated_hypothesis_ids: ["control", "challenger"],
    primary_outcome: {
      name: "setup_completed",
      scope: "estimated_treatment_effect",
      window_hours: 72,
      causal_estimand: "difference in setup completion probability",
    },
    allocation_method: "server_randomized_complete_blocks_v1",
    causal_treatment_hypothesis_id: "challenger",
    minimum_eligible_blocks: 6,
    maximum_posts: 12,
    minimum_attribution_coverage_basis_points: 8_000,
  };
}

function observations(blocks = 6) {
  return Array.from({ length: blocks }, (_, index) => index + 1).flatMap((block) => [
    {
      assignment_id: `assignment-${block}-control`,
      eligible_block_id: `block-${block}`,
      hypothesis_id: "control",
      publication_id: `publication-${block}-control`,
      eligible: true,
      attribution_observed: true,
      converted: false,
      guardrail_failures: [],
    },
    {
      assignment_id: `assignment-${block}-challenger`,
      eligible_block_id: `block-${block}`,
      hypothesis_id: "challenger",
      publication_id: `publication-${block}-challenger`,
      product_event_id: `event-${block}-challenger`,
      eligible: true,
      attribution_observed: true,
      converted: true,
      guardrail_failures: [],
    },
  ]);
}

function request(blocks = 6) {
  const registered = registration();
  registered.minimum_eligible_blocks = blocks;
  registered.maximum_posts = blocks * 2;
  return {
    evaluation_id: "evaluation-causal-1",
    campaign_id: "campaign-causal-1",
    registration: registered,
    observations: observations(blocks),
    randomization_seed_sha256: "d".repeat(64),
    causal_exposure_verified: true,
    windows_complete: true,
    evaluated_at: "2026-09-02T00:00:00Z",
  };
}

test("server-randomized complete blocks use an exact pre-registered effect estimator", () => {
  const evaluation = deriveExperimentEvaluation(request());

  assert.equal(evaluation.state, "evaluated");
  assert.equal(evaluation.winner_hypothesis_id, "challenger");
  assert.deepEqual(evaluation.causal_estimate, {
    schema_version: "trace.causal-effect-estimate.v1",
    estimator: "randomized_complete_blocks_risk_difference.v1",
    control_hypothesis_id: "control",
    treatment_hypothesis_id: "challenger",
    randomization_seed_sha256: "d".repeat(64),
    treatment_minus_control_basis_points: 10_000,
    two_sided_p_value_basis_points: 312,
    decision_threshold_basis_points: 500,
  });
});

test("randomized effects without enough exact evidence remain inconclusive", () => {
  const evaluation = deriveExperimentEvaluation(request(2));

  assert.equal(evaluation.state, "inconclusive");
  assert.equal(evaluation.winner_hypothesis_id, null);
  assert.equal(evaluation.causal_estimate.two_sided_p_value_basis_points, 5_000);
});

test("causal evaluation remains fail-closed until exposure slots are server-verified", () => {
  const causalRequest = request();
  causalRequest.causal_exposure_verified = false;

  const evaluation = deriveExperimentEvaluation(causalRequest);

  assert.equal(evaluation.state, "inconclusive");
  assert.equal(evaluation.causal_estimate, null);
  assert.match(evaluation.interpretation, /exposure slots/);
});

test("a causal scope fails closed without a server randomization receipt", () => {
  const causalRequest = request();
  causalRequest.randomization_seed_sha256 = null;

  assert.throws(
    () => deriveExperimentEvaluation(causalRequest),
    /randomization seed lineage/,
  );
});

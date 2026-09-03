import assert from "node:assert/strict";
import test from "node:test";

import {
  buildOutcomeReassessmentTask,
  deriveReassessmentSituation,
  InvalidOutcomeReassessment,
  validateOutcomeReassessment,
} from "../src/marketing-outcome-reassessment.js";

const SHA = "a".repeat(64);
const NOW = "2026-09-02T00:00:00.000Z";

function strategy() {
  return {
    schema_version: "trace.strategy-brief.v1",
    brief_id: "brief-1",
    campaign_id: "campaign-1",
    account_id: "trace_kr",
    decision_dossier: {
      schema_version: "trace.marketing-decision-dossier.v1",
      situation: "new_launch",
      selected_icp_id: "ios-character-fans",
      selection_basis_ids: ["signal-1"],
      positioning: {
        category: "dynamic lock-screen companion",
        current_alternative: "a static lock-screen image",
        differentiated_mechanism: "one character changes with the day",
        proof_claim_ids: ["claim-1"],
      },
      evidence_dispositions: [{
        evidence_id: "signal-1",
        disposition: "supports",
        confidence_basis_points: 7000,
        freshness: "fresh",
        use: "use_as_constraint",
        reason: "The approved signal supports this audience.",
      }],
      recommended_next_step: "design_experiment",
      reason: "The audience has an approved basis.",
      required_proof_ids: ["claim-1"],
    },
    hypotheses: [
      { hypothesis_id: "control", role: "control", claim_ids: ["claim-1"] },
      { hypothesis_id: "challenger", role: "challenger", claim_ids: ["claim-1"] },
    ],
    experiment: { experiment_id: "experiment-1" },
  };
}

function evaluation(overrides = {}) {
  return {
    schema_version: "trace.experiment-evaluation.v1",
    evaluation_id: "evaluation-1",
    campaign_id: "campaign-1",
    experiment_id: "experiment-1",
    state: "evaluated",
    winner_hypothesis_id: "challenger",
    guardrail_failures: [],
    evaluated_at: NOW,
    ...overrides,
  };
}

function reassessment(situation = "experiment_result") {
  return {
    schema_version: "trace.marketing-reassessment.v1",
    reassessment_id: `reassessment-${SHA.slice(0, 48)}`,
    campaign_id: "campaign-1",
    trigger_evaluation_id: "evaluation-1",
    trigger_evaluation_sha256: SHA,
    situation,
    decision_dossier: {
      schema_version: "trace.marketing-decision-dossier.v1",
      situation,
      selected_icp_id: "ios-character-fans",
      selection_basis_ids: ["signal-1"],
      positioning: strategy().decision_dossier.positioning,
      evidence_dispositions: [
        strategy().decision_dossier.evidence_dispositions[0],
        {
          evidence_id: "evaluation-1",
          disposition: "supports",
          confidence_basis_points: 10000,
          freshness: "fresh",
          use: "use_as_constraint",
          reason: "The bound evaluation is the newest performance signal.",
        },
      ],
      recommended_next_step: "design_experiment",
      reason: "Retain the audience and revise only the observed weak hypothesis.",
      required_proof_ids: ["claim-1", "evaluation-1"],
    },
    hypothesis_reassessments: [
      {
        hypothesis_id: "control",
        disposition: "retain",
        rationale: "It remains the comparison baseline.",
        next_test: "Retain the same control in the next registered block.",
      },
      {
        hypothesis_id: "challenger",
        disposition: "revise",
        rationale: "Its outcome supports a narrower follow-up.",
        next_test: "Change only the opening value frame.",
      },
    ],
    unanswered_questions: ["Does the effect replicate in another posting block?"],
    created_at: NOW,
  };
}

test("live outcome state routes dynamically before the model decides the response", () => {
  const prior = strategy();
  assert.equal(deriveReassessmentSituation(evaluation(), prior), "experiment_result");
  assert.equal(
    deriveReassessmentSituation(evaluation({ winner_hypothesis_id: "control" }), prior),
    "performance_regression",
  );
  assert.equal(
    deriveReassessmentSituation(evaluation({
      state: "stopped",
      winner_hypothesis_id: null,
      guardrail_failures: ["publication_unknown_side_effect"],
    }), prior),
    "tool_failure",
  );
});

test("an immutable evaluation creates one bounded no-effect reassessment task", () => {
  const task = buildOutcomeReassessmentTask({
    accountId: "trace_kr",
    campaignId: "campaign-1",
    priorStrategy: strategy(),
    priorStrategySha256: "b".repeat(64),
    evaluation: evaluation(),
    evaluationSha256: SHA,
    supportedClaimIds: ["claim-1"],
    taskId: "task-1",
  });

  assert.equal(task.payload.judgment, "outcome_reassessment");
  assert.equal(task.payload.situation, "experiment_result");
  assert.equal(task.created_at, NOW);
  assert.equal(task.credential_ref, null);
});

test("reassessment validation preserves source evidence and hypothesis coverage", () => {
  const payload = buildOutcomeReassessmentTask({
    accountId: "trace_kr",
    campaignId: "campaign-1",
    priorStrategy: strategy(),
    priorStrategySha256: "b".repeat(64),
    evaluation: evaluation(),
    evaluationSha256: SHA,
    supportedClaimIds: ["claim-1"],
    taskId: "task-1",
  }).payload;
  assert.doesNotThrow(() => validateOutcomeReassessment(payload, reassessment()));

  const inventedIcp = reassessment();
  inventedIcp.decision_dossier.selected_icp_id = "invented-segment";
  assert.throws(
    () => validateOutcomeReassessment(payload, inventedIcp),
    InvalidOutcomeReassessment,
  );

  const missingHypothesis = reassessment();
  missingHypothesis.hypothesis_reassessments.pop();
  assert.throws(
    () => validateOutcomeReassessment(payload, missingHypothesis),
    InvalidOutcomeReassessment,
  );
});

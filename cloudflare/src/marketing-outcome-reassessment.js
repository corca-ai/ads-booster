const SUPPORTED_CLAIM_STATES = new Set([
  "source_supported",
  "build_bound",
  "installed_confirmed",
]);

export function supportedClaimIdsFromPacket(packet) {
  return requireArray(packet?.claims, "feature packet claims")
    .filter((claim) => SUPPORTED_CLAIM_STATES.has(claim?.status))
    .map((claim) => requiredId(claim?.claim_id, "claim ID"))
    .filter((claimId, index, claimIds) => claimIds.indexOf(claimId) === index)
    .sort();
}

export function deriveReassessmentSituation(evaluation, priorStrategy) {
  const failures = requireArray(evaluation?.guardrail_failures, "evaluation guardrail failures");
  if (failures.some((failure) => (
    typeof failure === "string" && failure.includes("unknown_side_effect")
  ))) {
    return "tool_failure";
  }
  const hypotheses = requireArray(priorStrategy?.hypotheses, "strategy hypotheses");
  const control = hypotheses.find((hypothesis) => hypothesis?.role === "control");
  const controlId = requiredId(control?.hypothesis_id, "control hypothesis ID");
  if (evaluation?.state === "stopped" || evaluation?.winner_hypothesis_id === controlId) {
    return "performance_regression";
  }
  return "experiment_result";
}

export function buildOutcomeReassessmentTask({
  accountId,
  campaignId,
  priorStrategy,
  priorStrategySha256,
  evaluation,
  evaluationSha256,
  supportedClaimIds,
  taskId = crypto.randomUUID(),
}) {
  if (
    priorStrategy?.campaign_id !== campaignId
    || priorStrategy?.account_id !== accountId
    || evaluation?.campaign_id !== campaignId
    || evaluation?.experiment_id !== priorStrategy?.experiment?.experiment_id
  ) {
    throw new InvalidOutcomeReassessment("reassessment lineage is invalid");
  }
  requireObject(priorStrategy.decision_dossier, "prior decision dossier");
  const normalizedSupportedClaimIds = uniqueIds(supportedClaimIds, "supported claim IDs");
  const strategyClaimIds = new Set(
    requireArray(priorStrategy.hypotheses, "strategy hypotheses")
      .flatMap((hypothesis) => uniqueIds(hypothesis?.claim_ids, "hypothesis claim IDs")),
  );
  const supported = new Set(normalizedSupportedClaimIds);
  if (![...strategyClaimIds].every((claimId) => supported.has(claimId))) {
    throw new InvalidOutcomeReassessment("prior strategy claims are no longer supported");
  }
  const situation = deriveReassessmentSituation(evaluation, priorStrategy);
  const reassessmentId = `reassessment-${evaluationSha256.slice(0, 48)}`;
  const createdAt = requiredTimestamp(evaluation?.evaluated_at, "evaluation evaluated_at");
  return {
    schema_version: "1",
    task_id: taskId,
    run_id: `outcome-reassessment-${taskId}`,
    account_id: requiredId(accountId, "account ID"),
    kind: "marketing_judgment",
    idempotency_key: `outcome-reassessment:${accountId}:${evaluation.evaluation_id}`,
    payload: {
      pipeline: "hosted_marketing_judgment_v1",
      judgment: "outcome_reassessment",
      reassessment_id: reassessmentId,
      campaign_id: requiredId(campaignId, "campaign ID"),
      account_id: accountId,
      situation,
      prior_strategy: priorStrategy,
      prior_strategy_sha256: requiredSha256(priorStrategySha256, "strategy brief SHA"),
      evaluation,
      evaluation_sha256: requiredSha256(evaluationSha256, "evaluation SHA"),
      supported_claim_ids: normalizedSupportedClaimIds,
      requested_by: "hosted_workspace",
    },
    created_at: createdAt,
    credential_ref: null,
  };
}

export function validateOutcomeReassessment(payload, reassessment) {
  const prior = requireObject(payload?.prior_strategy, "prior strategy");
  const evaluation = requireObject(payload?.evaluation, "evaluation");
  const dossier = requireObject(reassessment?.decision_dossier, "decision dossier");
  const expectedSituation = deriveReassessmentSituation(evaluation, prior);
  if (
    reassessment?.schema_version !== "trace.marketing-reassessment.v1"
    || reassessment.reassessment_id !== payload.reassessment_id
    || reassessment.campaign_id !== payload.campaign_id
    || reassessment.trigger_evaluation_id !== evaluation.evaluation_id
    || reassessment.trigger_evaluation_sha256 !== payload.evaluation_sha256
    || reassessment.situation !== expectedSituation
    || dossier.situation !== expectedSituation
    || dossier.schema_version !== "trace.marketing-decision-dossier.v1"
    || reassessment.created_at !== evaluation.evaluated_at
  ) {
    throw new InvalidOutcomeReassessment("reassessment identity is invalid");
  }
  const priorDossier = requireObject(prior.decision_dossier, "prior decision dossier");
  validateDecisionSemantics(dossier, expectedSituation);
  if (![priorDossier.selected_icp_id, "research_needed"].includes(dossier.selected_icp_id)) {
    throw new InvalidOutcomeReassessment("reassessment invented an ICP");
  }
  const supportedClaimIds = new Set(uniqueIds(payload.supported_claim_ids, "supported claim IDs"));
  const positioning = requireObject(dossier.positioning, "reassessment positioning");
  if (!uniqueIds(positioning.proof_claim_ids, "positioning proof claim IDs")
    .every((claimId) => supportedClaimIds.has(claimId))) {
    throw new InvalidOutcomeReassessment("reassessment uses an unsupported claim");
  }
  const priorDispositions = dispositionMap(priorDossier.evidence_dispositions);
  const dispositions = dispositionMap(dossier.evidence_dispositions);
  const requiredEvidenceIds = new Set([...priorDispositions.keys(), evaluation.evaluation_id]);
  if (!sameSet(new Set(dispositions.keys()), requiredEvidenceIds)) {
    throw new InvalidOutcomeReassessment("reassessment evidence is incomplete");
  }
  for (const [evidenceId, before] of priorDispositions) {
    const after = dispositions.get(evidenceId);
    if (
      after.freshness !== before.freshness
      || after.confidence_basis_points !== before.confidence_basis_points
    ) {
      throw new InvalidOutcomeReassessment("reassessment rewrote evidence metadata");
    }
  }
  const evaluationDisposition = dispositions.get(evaluation.evaluation_id);
  if (
    evaluationDisposition.freshness !== "fresh"
    || evaluationDisposition.confidence_basis_points !== 10_000
    || evaluationDisposition.use === "exclude"
  ) {
    throw new InvalidOutcomeReassessment("reassessment rewrote its evaluation");
  }
  const basisIds = uniqueIds(dossier.selection_basis_ids ?? [], "selection basis IDs");
  if (!basisIds.every((evidenceId) => requiredEvidenceIds.has(evidenceId))) {
    throw new InvalidOutcomeReassessment("reassessment ICP basis is unbound");
  }
  if (
    dossier.selected_icp_id === priorDossier.selected_icp_id
    && dossier.selected_icp_id !== "research_needed"
  ) {
    const priorBasis = new Set(uniqueIds(priorDossier.selection_basis_ids ?? [], "prior basis IDs"));
    if (!basisIds.some((evidenceId) => priorBasis.has(evidenceId))) {
      throw new InvalidOutcomeReassessment("reassessment dropped its ICP basis");
    }
  }
  const allowedProofIds = new Set([...supportedClaimIds, ...requiredEvidenceIds]);
  if (!uniqueIds(dossier.required_proof_ids ?? [], "required proof IDs")
    .every((proofId) => allowedProofIds.has(proofId))) {
    throw new InvalidOutcomeReassessment("reassessment proof is unbound");
  }
  const expectedHypotheses = new Set(
    requireArray(prior.hypotheses, "strategy hypotheses")
      .map((hypothesis) => requiredId(hypothesis?.hypothesis_id, "hypothesis ID")),
  );
  const reviews = requireArray(
    reassessment.hypothesis_reassessments,
    "hypothesis reassessments",
  );
  if (reviews.length < 2 || reviews.length > 8) {
    throw new InvalidOutcomeReassessment("hypothesis reassessment count is invalid");
  }
  for (const review of reviews) validateHypothesisReview(review);
  const reviewedHypotheses = new Set(
    reviews.map((review) => requiredId(review?.hypothesis_id, "reviewed hypothesis ID")),
  );
  if (reviews.length !== reviewedHypotheses.size || !sameSet(reviewedHypotheses, expectedHypotheses)) {
    throw new InvalidOutcomeReassessment("reassessment hypotheses are incomplete");
  }
  if (
    expectedSituation === "tool_failure"
    && reviews.some((review) => review.disposition !== "retain" || review.next_test != null)
  ) {
    throw new InvalidOutcomeReassessment("tool effect must be reconciled before strategy changes");
  }
  const questions = requireArray(reassessment.unanswered_questions ?? [], "unanswered questions");
  if (questions.length > 16) {
    throw new InvalidOutcomeReassessment("too many unanswered questions");
  }
  for (const question of questions) requiredString(question, "unanswered question", 1500);
}

export class InvalidOutcomeReassessment extends Error {}

function dispositionMap(value) {
  const items = requireArray(value, "evidence dispositions");
  const result = new Map();
  for (const item of items) {
    const evidenceId = requiredId(item?.evidence_id, "evidence disposition ID");
    if (result.has(evidenceId)) {
      throw new InvalidOutcomeReassessment("evidence dispositions must be unique");
    }
    const disposition = requireObject(item, "evidence disposition");
    if (!["supports", "contradicts", "insufficient"].includes(disposition.disposition)) {
      throw new InvalidOutcomeReassessment("evidence disposition is invalid");
    }
    if (
      !Number.isInteger(disposition.confidence_basis_points)
      || disposition.confidence_basis_points < 0
      || disposition.confidence_basis_points > 10_000
      || !["fresh", "stale", "unknown"].includes(disposition.freshness)
      || !["use_as_constraint", "test", "exclude"].includes(disposition.use)
    ) {
      throw new InvalidOutcomeReassessment("evidence metadata is invalid");
    }
    requiredString(disposition.reason, "evidence disposition reason", 1000);
    if (disposition.freshness === "stale" && disposition.use !== "exclude") {
      throw new InvalidOutcomeReassessment("stale evidence must be excluded");
    }
    result.set(evidenceId, disposition);
  }
  return result;
}

function validateDecisionSemantics(dossier, situation) {
  requiredId(dossier.selected_icp_id, "selected ICP ID");
  requiredString(dossier.reason, "decision reason", 1500);
  const positioning = requireObject(dossier.positioning, "positioning");
  requiredString(positioning.category, "positioning category", 500);
  requiredString(positioning.current_alternative, "current alternative", 1000);
  requiredString(positioning.differentiated_mechanism, "differentiated mechanism", 1500);
  const steps = ["research", "design_experiment", "hold_for_review", "reconcile_effect"];
  if (!steps.includes(dossier.recommended_next_step)) {
    throw new InvalidOutcomeReassessment("recommended next step is invalid");
  }
  if (
    (situation === "tool_failure") !== (dossier.recommended_next_step === "reconcile_effect")
  ) {
    throw new InvalidOutcomeReassessment("recommended next step is unsafe for the situation");
  }
  if (
    dossier.selected_icp_id === "research_needed"
    && !["research", "hold_for_review"].includes(dossier.recommended_next_step)
  ) {
    throw new InvalidOutcomeReassessment("an unresolved ICP cannot start an experiment");
  }
}

function validateHypothesisReview(value) {
  const review = requireObject(value, "hypothesis reassessment");
  requiredId(review.hypothesis_id, "hypothesis reassessment ID");
  if (!["retain", "revise", "retire"].includes(review.disposition)) {
    throw new InvalidOutcomeReassessment("hypothesis disposition is invalid");
  }
  requiredString(review.rationale, "hypothesis rationale", 1500);
  if (review.next_test != null) requiredString(review.next_test, "hypothesis next test", 1500);
}

function sameSet(left, right) {
  return left.size === right.size && [...left].every((item) => right.has(item));
}

function uniqueIds(value, name) {
  const ids = requireArray(value, name).map((item) => requiredId(item, name));
  if (new Set(ids).size !== ids.length) throw new InvalidOutcomeReassessment(`${name} repeat`);
  return ids;
}

function requireArray(value, name) {
  if (!Array.isArray(value)) throw new InvalidOutcomeReassessment(`${name} is invalid`);
  return value;
}

function requireObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new InvalidOutcomeReassessment(`${name} is invalid`);
  }
  return value;
}

function requiredId(value, name) {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)) {
    throw new InvalidOutcomeReassessment(`${name} is invalid`);
  }
  return value;
}

function requiredSha256(value, name) {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) {
    throw new InvalidOutcomeReassessment(`${name} is invalid`);
  }
  return value;
}

function requiredTimestamp(value, name) {
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) {
    throw new InvalidOutcomeReassessment(`${name} is invalid`);
  }
  return value;
}

function requiredString(value, name, maximum) {
  if (typeof value !== "string" || !value.trim() || value.length > maximum) {
    throw new InvalidOutcomeReassessment(`${name} is invalid`);
  }
  return value;
}

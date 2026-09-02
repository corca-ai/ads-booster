const DIRECT_RESPONSE_ATTRIBUTION = "direct_response_attribution";
const ESTIMATED_TREATMENT_EFFECT = "estimated_treatment_effect";
const BALANCED_COMPLETE_BLOCKS = "balanced_complete_blocks";
const SERVER_RANDOMIZED_COMPLETE_BLOCKS_V1 = "server_randomized_complete_blocks_v1";
const CAUSAL_DECISION_THRESHOLD_BASIS_POINTS = 500;

/**
 * Re-derive an experiment result from the immutable request dispatched to the
 * deterministic Mac worker. This is intentionally a pure, local counterpart
 * to `marketing.experiment_evaluation.evaluate_experiment`, not a second
 * source of marketing judgment.
 */
export function deriveExperimentEvaluation(value) {
  const request = requireObject(value, "evaluation request");
  const registration = requireObject(request.registration, "evaluation registration");
  const primaryOutcome = requireObject(registration.primary_outcome, "primary outcome");
  const observations = requireArray(request.observations, "evaluation observations");
  const activeHypotheses = requireIdentifierArray(
    registration.activated_hypothesis_ids,
    "activated hypothesis IDs",
    { minimum: 2 },
  );
  const active = new Set(activeHypotheses);
  const minimumEligibleBlocks = requireInteger(
    registration.minimum_eligible_blocks,
    "minimum eligible blocks",
    { minimum: 2 },
  );
  const coverageMinimum = requireInteger(
    registration.minimum_attribution_coverage_basis_points,
    "minimum attribution coverage",
    { minimum: 0, maximum: 10_000 },
  );
  const outcomeScope = requiredOneOf(
    primaryOutcome.scope,
    "primary outcome scope",
    [DIRECT_RESPONSE_ATTRIBUTION, ESTIMATED_TREATMENT_EFFECT],
  );
  const allocationMethod = registration.allocation_method ?? BALANCED_COMPLETE_BLOCKS;
  const causalTreatmentHypothesisId = registration.causal_treatment_hypothesis_id ?? null;
  const randomizationSeedSha256 = request.randomization_seed_sha256 ?? null;
  const causalExposureVerified = request.causal_exposure_verified === true;
  if (
    request.causal_exposure_verified != null
    && typeof request.causal_exposure_verified !== "boolean"
  ) {
    throw new InvalidExperimentEvaluationRequest("causal exposure verification is invalid");
  }
  validateRegisteredEstimator({
    outcomeScope,
    allocationMethod,
    causalTreatmentHypothesisId,
    randomizationSeedSha256,
    activeHypotheses,
    minimumEligibleBlocks,
    maximumPosts: registration.maximum_posts,
  });
  const normalized = observations.map((observation) => normalizeObservation(observation, active));
  const assignmentIds = normalized.map((observation) => observation.assignment_id);
  if (new Set(assignmentIds).size !== assignmentIds.length) {
    throw new InvalidExperimentEvaluationRequest("assignment IDs must be unique");
  }

  const eligible = normalized.filter((observation) => observation.eligible);
  const guardrailFailures = [...new Set(
    normalized.flatMap((observation) => observation.guardrail_failures),
  )].sort();
  const completeBlockIds = completeBlocks(eligible, active);
  const included = eligible.filter((observation) => completeBlockIds.has(observation.eligible_block_id));
  const coverageBasisPoints = included.length
    ? roundHalfEven(10_000 * included.filter((observation) => observation.attribution_observed).length
      / included.length)
    : 0;
  const lineageIds = included.length
    ? included.map((observation) => observation.assignment_id)
    : [requiredIdentifier(request.evaluation_id, "evaluation ID")];

  let state;
  let winnerHypothesisId = null;
  let interpretation;
  let causalEstimate = null;
  if (guardrailFailures.length) {
    state = "stopped";
    interpretation = "Guardrail failure stopped the experiment; no winner is named.";
  } else if (request.windows_complete !== true) {
    state = "inconclusive";
    interpretation = "Registered observation windows are incomplete.";
  } else if (completeBlockIds.size < minimumEligibleBlocks) {
    state = "inconclusive";
    interpretation = "The minimum number of complete eligible blocks was not reached.";
  } else if (coverageBasisPoints < coverageMinimum) {
    state = "inconclusive";
    interpretation = "Attribution coverage is below the pre-registered minimum.";
  } else if ([...active].some((hypothesisId) => !included.some((observation) => (
    observation.attribution_observed && observation.hypothesis_id === hypothesisId
  )))) {
    state = "inconclusive";
    interpretation = "At least one active hypothesis has no observed attribution.";
  } else if (
    outcomeScope === ESTIMATED_TREATMENT_EFFECT
    && completeBlockIds.size !== minimumEligibleBlocks
  ) {
    state = "inconclusive";
    interpretation = "The pre-registered causal sample size was not completed exactly.";
  } else if (
    outcomeScope === ESTIMATED_TREATMENT_EFFECT
    && !causalExposureVerified
  ) {
    state = "inconclusive";
    interpretation = "Server-committed exposure slots have not been verified; no causal estimate is named.";
  } else if (outcomeScope === ESTIMATED_TREATMENT_EFFECT) {
    causalEstimate = randomizedBlockEffectEstimate(
      included,
      causalTreatmentHypothesisId,
      active,
      randomizationSeedSha256,
    );
    const effect = causalEstimate.treatment_minus_control_basis_points;
    if (
      effect !== 0
      && causalEstimate.two_sided_p_value_basis_points
        <= causalEstimate.decision_threshold_basis_points
    ) {
      state = "evaluated";
      winnerHypothesisId = effect > 0
        ? causalEstimate.treatment_hypothesis_id
        : causalEstimate.control_hypothesis_id;
      interpretation = `${winnerHypothesisId} won the pre-registered randomized complete-block comparison `
        + `with a ${effect} basis-point treatment-minus-control effect and an exact two-sided `
        + "randomization test at or below the registered threshold.";
    } else {
      state = "inconclusive";
      interpretation = "The pre-registered randomized complete-block estimate did not clear "
        + "the exact two-sided decision threshold; no causal winner is named.";
    }
  } else {
    const rates = descriptiveConversionRates(included, active);
    const topRate = Math.max(...rates.values());
    const winners = [...rates.entries()]
      .filter(([, rate]) => rate === topRate)
      .map(([hypothesisId]) => hypothesisId);
    if (winners.length === 1) {
      state = "evaluated";
      winnerHypothesisId = winners[0];
      interpretation = `${winnerHypothesisId} has the highest observed direct-response attribution rate `
        + "inside complete eligible blocks. This is descriptive attribution, not a causal effect.";
    } else {
      state = "inconclusive";
      interpretation = "Direct-response attribution is tied across active hypotheses.";
    }
  }

  return {
    schema_version: "trace.experiment-evaluation.v1",
    evaluation_id: requiredIdentifier(request.evaluation_id, "evaluation ID"),
    campaign_id: requiredIdentifier(request.campaign_id, "campaign ID"),
    experiment_id: requiredIdentifier(registration.experiment_id, "experiment ID"),
    state,
    outcome_scope: outcomeScope,
    eligible_blocks: completeBlockIds.size,
    attribution_coverage_basis_points: coverageBasisPoints,
    winner_hypothesis_id: winnerHypothesisId,
    causal_estimate: causalEstimate,
    interpretation,
    guardrail_failures: guardrailFailures,
    lineage_ids: lineageIds,
    evaluated_at: requiredTimestamp(request.evaluated_at, "evaluated_at"),
  };
}

export class InvalidExperimentEvaluationRequest extends Error {}

function normalizeObservation(value, active) {
  const observation = requireObject(value, "evaluation observation");
  const assignmentId = requiredIdentifier(observation.assignment_id, "assignment ID");
  const hypothesisId = requiredIdentifier(observation.hypothesis_id, "hypothesis ID");
  if (!active.has(hypothesisId)) {
    throw new InvalidExperimentEvaluationRequest("observation uses an inactive hypothesis");
  }
  const attributionObserved = requireBoolean(
    observation.attribution_observed,
    "attribution observed",
  );
  const converted = observation.converted;
  if (attributionObserved !== (converted !== null && converted !== undefined)) {
    throw new InvalidExperimentEvaluationRequest("observed attribution must state whether it converted");
  }
  if (converted !== null && converted !== undefined && typeof converted !== "boolean") {
    throw new InvalidExperimentEvaluationRequest("converted is invalid");
  }
  if (observation.product_event_id != null && converted !== true) {
    throw new InvalidExperimentEvaluationRequest("a product event requires a converted observation");
  }
  if (attributionObserved && observation.publication_id == null) {
    throw new InvalidExperimentEvaluationRequest("an observed attribution requires a publication");
  }
  return {
    assignment_id: assignmentId,
    eligible_block_id: requiredIdentifier(observation.eligible_block_id, "eligible block ID"),
    hypothesis_id: hypothesisId,
    eligible: requireBoolean(observation.eligible, "eligible"),
    attribution_observed: attributionObserved,
    converted: converted ?? null,
    guardrail_failures: requireStringArray(observation.guardrail_failures, "guardrail failures"),
  };
}

function completeBlocks(observations, active) {
  const hypothesesByBlock = new Map();
  for (const observation of observations) {
    const hypotheses = hypothesesByBlock.get(observation.eligible_block_id) ?? [];
    hypotheses.push(observation.hypothesis_id);
    hypothesesByBlock.set(observation.eligible_block_id, hypotheses);
  }
  return new Set([...hypothesesByBlock.entries()]
    .filter(([, hypotheses]) => (
      new Set(hypotheses).size === active.size
      && hypotheses.length === active.size
      && [...active].every((hypothesisId) => hypotheses.includes(hypothesisId))
    ))
    .map(([blockId]) => blockId));
}

function descriptiveConversionRates(observations, active) {
  const totals = new Map([...active].map((hypothesisId) => [hypothesisId, 0]));
  const conversions = new Map([...active].map((hypothesisId) => [hypothesisId, 0]));
  for (const observation of observations) {
    if (!observation.attribution_observed) continue;
    totals.set(observation.hypothesis_id, totals.get(observation.hypothesis_id) + 1);
    conversions.set(
      observation.hypothesis_id,
      conversions.get(observation.hypothesis_id) + Number(observation.converted),
    );
  }
  if ([...totals.values()].some((total) => total === 0)) {
    throw new InvalidExperimentEvaluationRequest(
      "every active hypothesis needs observed attribution",
    );
  }
  return new Map([...active].map((hypothesisId) => [
    hypothesisId,
    conversions.get(hypothesisId) / totals.get(hypothesisId),
  ]));
}

function validateRegisteredEstimator({
  outcomeScope,
  allocationMethod,
  causalTreatmentHypothesisId,
  randomizationSeedSha256,
  activeHypotheses,
  minimumEligibleBlocks,
  maximumPosts,
}) {
  if (outcomeScope === ESTIMATED_TREATMENT_EFFECT) {
    if (allocationMethod !== SERVER_RANDOMIZED_COMPLETE_BLOCKS_V1) {
      throw new InvalidExperimentEvaluationRequest(
        "estimated treatment effects require server-randomized complete blocks",
      );
    }
    if (activeHypotheses.length !== 2) {
      throw new InvalidExperimentEvaluationRequest(
        "randomized block estimator requires exactly two active hypotheses",
      );
    }
    if (
      !Number.isInteger(maximumPosts)
      || maximumPosts !== minimumEligibleBlocks * activeHypotheses.length
    ) {
      throw new InvalidExperimentEvaluationRequest(
        "estimated treatment effects require one fixed complete block per post pair",
      );
    }
    if (
      typeof causalTreatmentHypothesisId !== "string"
      || !activeHypotheses.includes(causalTreatmentHypothesisId)
    ) {
      throw new InvalidExperimentEvaluationRequest(
        "estimated treatment effects require one active treatment hypothesis",
      );
    }
    if (typeof randomizationSeedSha256 !== "string" || !/^[a-f0-9]{64}$/.test(randomizationSeedSha256)) {
      throw new InvalidExperimentEvaluationRequest(
        "causal evaluation requires randomization seed lineage",
      );
    }
    return;
  }
  if (
    allocationMethod !== BALANCED_COMPLETE_BLOCKS
    || causalTreatmentHypothesisId !== null
    || randomizationSeedSha256 !== null
  ) {
    throw new InvalidExperimentEvaluationRequest(
      "direct-response attribution cannot register a causal estimator",
    );
  }
}

function randomizedBlockEffectEstimate(
  observations,
  treatmentHypothesisId,
  active,
  randomizationSeedSha256,
) {
  const controlHypothesisId = [...active].find((hypothesisId) => (
    hypothesisId !== treatmentHypothesisId
  ));
  if (!controlHypothesisId || active.size !== 2) {
    throw new InvalidExperimentEvaluationRequest(
      "randomized block estimator requires distinct control and treatment hypotheses",
    );
  }
  const outcomesByBlock = new Map();
  for (const observation of observations) {
    if (!observation.attribution_observed || observation.converted == null) {
      throw new InvalidExperimentEvaluationRequest(
        "every causal block needs observed attribution",
      );
    }
    const outcomes = outcomesByBlock.get(observation.eligible_block_id) ?? new Map();
    outcomes.set(observation.hypothesis_id, observation.converted);
    outcomesByBlock.set(observation.eligible_block_id, outcomes);
  }
  const differences = [...outcomesByBlock.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([, outcomes]) => {
      if (outcomes.size !== active.size || [...active].some((id) => !outcomes.has(id))) {
        throw new InvalidExperimentEvaluationRequest(
          "randomized block estimator requires complete two-arm blocks",
        );
      }
      return Number(outcomes.get(treatmentHypothesisId)) - Number(outcomes.get(controlHypothesisId));
    });
  const signedSum = differences.reduce((sum, difference) => sum + difference, 0);
  const nonzeroPairs = differences.filter((difference) => difference !== 0).length;
  return {
    schema_version: "trace.causal-effect-estimate.v1",
    estimator: "randomized_complete_blocks_risk_difference.v1",
    control_hypothesis_id: controlHypothesisId,
    treatment_hypothesis_id: treatmentHypothesisId,
    randomization_seed_sha256: randomizationSeedSha256,
    treatment_minus_control_basis_points: roundHalfEven(10_000 * signedSum / differences.length),
    two_sided_p_value_basis_points: exactTwoSidedSignFlipPValueBasisPoints(
      signedSum,
      nonzeroPairs,
    ),
    decision_threshold_basis_points: CAUSAL_DECISION_THRESHOLD_BASIS_POINTS,
  };
}

function exactTwoSidedSignFlipPValueBasisPoints(signedSum, nonzeroPairs) {
  if (nonzeroPairs === 0) return 10_000;
  let asOrMoreExtreme = 0n;
  for (let positiveSigns = 0; positiveSigns <= nonzeroPairs; positiveSigns += 1) {
    if (Math.abs(2 * positiveSigns - nonzeroPairs) >= Math.abs(signedSum)) {
      asOrMoreExtreme += binomialCoefficient(nonzeroPairs, positiveSigns);
    }
  }
  return roundRationalHalfEven(
    asOrMoreExtreme * 10_000n,
    1n << BigInt(nonzeroPairs),
  );
}

function binomialCoefficient(total, selected) {
  const k = Math.min(selected, total - selected);
  let result = 1n;
  for (let index = 1; index <= k; index += 1) {
    result = (result * BigInt(total - k + index)) / BigInt(index);
  }
  return result;
}

function roundRationalHalfEven(numerator, denominator) {
  const lower = numerator / denominator;
  const remainder = numerator % denominator;
  if (remainder * 2n < denominator) return Number(lower);
  if (remainder * 2n > denominator) return Number(lower + 1n);
  return Number(lower % 2n === 0n ? lower : lower + 1n);
}

function roundHalfEven(value) {
  const lower = Math.floor(value);
  const fraction = value - lower;
  if (fraction < 0.5) return lower;
  if (fraction > 0.5) return lower + 1;
  return lower % 2 === 0 ? lower : lower + 1;
}

function requireObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new InvalidExperimentEvaluationRequest(`${name} is invalid`);
  }
  return value;
}

function requireArray(value, name) {
  if (!Array.isArray(value)) throw new InvalidExperimentEvaluationRequest(`${name} is invalid`);
  return value;
}

function requireIdentifierArray(value, name, { minimum = 0 } = {}) {
  const identifiers = requireArray(value, name).map((item) => requiredIdentifier(item, name));
  if (identifiers.length < minimum || new Set(identifiers).size !== identifiers.length) {
    throw new InvalidExperimentEvaluationRequest(`${name} are invalid`);
  }
  return identifiers;
}

function requireStringArray(value, name) {
  if (value == null) return [];
  return requireArray(value, name).map((item) => {
    if (typeof item !== "string") throw new InvalidExperimentEvaluationRequest(`${name} are invalid`);
    return item;
  });
}

function requiredIdentifier(value, name) {
  if (typeof value !== "string" || value.length === 0) {
    throw new InvalidExperimentEvaluationRequest(`${name} is invalid`);
  }
  return value;
}

function requiredOneOf(value, name, values) {
  if (!values.includes(value)) throw new InvalidExperimentEvaluationRequest(`${name} is invalid`);
  return value;
}

function requireInteger(value, name, { minimum = Number.MIN_SAFE_INTEGER, maximum = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new InvalidExperimentEvaluationRequest(`${name} is invalid`);
  }
  return value;
}

function requireBoolean(value, name) {
  if (typeof value !== "boolean") throw new InvalidExperimentEvaluationRequest(`${name} is invalid`);
  return value;
}

function requiredTimestamp(value, name) {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) {
    throw new InvalidExperimentEvaluationRequest(`${name} is invalid`);
  }
  return value;
}

const DIRECT_RESPONSE_ATTRIBUTION = "direct_response_attribution";
const ESTIMATED_TREATMENT_EFFECT = "estimated_treatment_effect";

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
  } else if (outcomeScope === ESTIMATED_TREATMENT_EFFECT) {
    state = "inconclusive";
    interpretation = "No eligible causal estimator is configured for this experiment.";
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

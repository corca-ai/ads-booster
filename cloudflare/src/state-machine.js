export const RUN_STATES = Object.freeze([
  "scheduled",
  "context_snapshot",
  "research",
  "planning",
  "candidate_generation",
  "awaiting_candidate_approval",
  "candidates_approved",
  "capture_requested",
  "capture_completed",
  "automatic_quality_check",
  "awaiting_human_approval",
  "approved",
  "rejected",
  "scheduled_for_publish",
  "publishing",
  "published",
  "observing",
  "evaluated",
  "memory_committed",
  "completed",
  "failed",
  "unknown_side_effect",
]);

const allowed = new Map([
  ["scheduled", ["context_snapshot", "failed"]],
  ["context_snapshot", ["research", "failed"]],
  ["research", ["planning", "failed"]],
  ["planning", ["candidate_generation", "failed"]],
  ["candidate_generation", ["awaiting_candidate_approval", "failed"]],
  ["awaiting_candidate_approval", ["candidates_approved", "rejected", "failed"]],
  ["candidates_approved", ["capture_requested"]],
  ["capture_requested", ["capture_completed", "failed"]],
  ["capture_completed", ["automatic_quality_check", "failed"]],
  ["automatic_quality_check", ["awaiting_human_approval", "failed"]],
  ["awaiting_human_approval", ["approved", "rejected", "failed"]],
  ["approved", ["scheduled_for_publish"]],
  ["rejected", []],
  ["scheduled_for_publish", ["publishing"]],
  ["publishing", ["published", "failed", "unknown_side_effect"]],
  ["published", ["observing"]],
  ["observing", ["evaluated", "failed"]],
  ["evaluated", ["memory_committed"]],
  ["memory_committed", ["completed"]],
  ["completed", []],
  ["failed", []],
  ["unknown_side_effect", []],
]);

export function assertTransition(from, to) {
  if (!allowed.get(from)?.includes(to)) {
    throw new Error(`invalid run transition ${from} -> ${to}`);
  }
}

export function taskCompletionEventType(kind, taskId) {
  const value = `task_${kind}_${taskId}`;
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(value)) {
    throw new Error(`invalid workflow event type ${value}`);
  }
  return value;
}

export function accountName(value) {
  if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value)) {
    throw new Error("account_id must be lower-case alphanumeric with _ or -");
  }
  return value;
}

export function approvalPhase(state, requested) {
  const expected =
    state === "awaiting_candidate_approval"
      ? "candidates"
      : state === "awaiting_human_approval"
        ? "publication"
        : null;
  if (expected === null) throw new Error(`run is not awaiting approval: ${state}`);
  if (requested !== undefined && requested !== expected) {
    throw new Error(`run is awaiting ${expected} approval`);
  }
  return expected;
}

export function normalizeCandidateIds(value) {
  if (
    !Array.isArray(value) ||
    value.length < 1 ||
    value.length > 8 ||
    value.some((item) => typeof item !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(item))
  ) {
    throw new Error("candidate_ids must contain 1-8 safe identifiers");
  }
  return [...new Set(value)];
}

export function selectedCandidateIds(selected, generated) {
  const candidateIds = normalizeCandidateIds(selected);
  const available = new Set(normalizeCandidateIds(generated));
  if (candidateIds.some((candidateId) => !available.has(candidateId))) {
    throw new Error("candidate approval selected an unknown candidate");
  }
  return candidateIds;
}

export function observationSchedule(value) {
  const parsed = [...new Set(
    String(value ?? "5,10,15,20,25,30")
      .split(",")
      .map(Number)
      .filter((item) => Number.isInteger(item) && item > 0),
  )].sort((left, right) => left - right);
  const minutes = parsed.length ? parsed : [5, 10, 15, 20, 25, 30];
  let previous = 0;
  return minutes.map((minute) => {
    const delay_minutes = minute - previous;
    previous = minute;
    return { minute, delay_minutes };
  });
}

export function assertRunnableAdapterMode(value) {
  if (value === "simulation") return value;
  if (value === "live") throw new Error("live adapter is not enabled");
  throw new Error("adapter_mode must be simulation or live");
}

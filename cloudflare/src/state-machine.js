export const RUN_STATES = Object.freeze([
  "scheduled",
  "context_snapshot",
  "research",
  "planning",
  "candidate_generation",
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
  ["candidate_generation", ["capture_requested", "failed"]],
  ["capture_requested", ["capture_completed", "failed"]],
  ["capture_completed", ["automatic_quality_check", "failed"]],
  ["automatic_quality_check", ["awaiting_human_approval", "failed"]],
  ["awaiting_human_approval", ["approved", "rejected"]],
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

export function taskEventType(kind) {
  const value = `task_${kind}_completed`;
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

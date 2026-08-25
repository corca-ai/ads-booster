import assert from "node:assert/strict";
import test from "node:test";

import { accountName, assertTransition, taskEventType } from "../src/state-machine.js";

test("accepts the complete happy-path transition chain", () => {
  const states = [
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
    "scheduled_for_publish",
    "publishing",
    "published",
    "observing",
    "evaluated",
    "memory_committed",
    "completed",
  ];
  for (let index = 1; index < states.length; index += 1) {
    assert.doesNotThrow(() => assertTransition(states[index - 1], states[index]));
  }
});

test("rejects bypassing human approval", () => {
  assert.throws(
    () => assertTransition("automatic_quality_check", "publishing"),
    /invalid run transition/,
  );
});

test("account and workflow event identifiers are safe", () => {
  assert.equal(accountName("trace_kr"), "trace_kr");
  assert.equal(taskEventType("generate_candidates"), "task_generate_candidates_completed");
  assert.throws(() => accountName("Trace KR"));
});

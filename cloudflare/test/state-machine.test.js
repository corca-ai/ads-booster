import assert from "node:assert/strict";
import test from "node:test";

import {
  accountName,
  approvalPhase,
  assertRunnableAdapterMode,
  assertTransition,
  normalizeCandidateIds,
  observationSchedule,
  selectedCandidateIds,
  taskCompletionEventType,
} from "../src/state-machine.js";

test("accepts the complete happy-path transition chain", () => {
  const states = [
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
    () => assertTransition("candidate_generation", "capture_requested"),
    /invalid run transition/,
  );
  assert.throws(
    () => assertTransition("automatic_quality_check", "publishing"),
    /invalid run transition/,
  );
});

test("account and workflow event identifiers are safe", () => {
  assert.equal(accountName("trace_kr"), "trace_kr");
  assert.equal(
    taskCompletionEventType("sample_metrics", "task-1"),
    "task_sample_metrics_task-1",
  );
  assert.throws(() => accountName("Trace KR"));
});

test("routes and validates the two approval phases", () => {
  assert.equal(approvalPhase("awaiting_candidate_approval"), "candidates");
  assert.equal(approvalPhase("awaiting_human_approval", "publication"), "publication");
  assert.throws(() => approvalPhase("awaiting_candidate_approval", "publication"));
  assert.deepEqual(normalizeCandidateIds(["candidate-1", "candidate-1"]), ["candidate-1"]);
  assert.deepEqual(
    selectedCandidateIds(["candidate-2"], ["candidate-1", "candidate-2"]),
    ["candidate-2"],
  );
  assert.throws(() => selectedCandidateIds(["unknown"], ["candidate-1"]));
});

test("turns absolute observation minutes into relative sleeps", () => {
  assert.deepEqual(observationSchedule("5,10,15,20,25,30"), [
    { minute: 5, delay_minutes: 5 },
    { minute: 10, delay_minutes: 5 },
    { minute: 15, delay_minutes: 5 },
    { minute: 20, delay_minutes: 5 },
    { minute: 25, delay_minutes: 5 },
    { minute: 30, delay_minutes: 5 },
  ]);
  assert.deepEqual(observationSchedule("20,5,20"), [
    { minute: 5, delay_minutes: 5 },
    { minute: 20, delay_minutes: 15 },
  ]);
});

test("fails closed until a live publication adapter exists", () => {
  assert.equal(assertRunnableAdapterMode("simulation"), "simulation");
  assert.throws(() => assertRunnableAdapterMode("live"), /not enabled/);
  assert.throws(() => assertRunnableAdapterMode("unknown"), /must be simulation or live/);
});

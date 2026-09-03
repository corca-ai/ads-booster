import assert from "node:assert/strict";
import test from "node:test";

import {
  CandidateImageInputsError,
  normalizeCandidateImageInputs,
} from "../src/candidate-image-inputs.js";

function weeklyImageInputs() {
  const colors = ["2D936C", "00B4D8", "F9C74F", "F26419", "DA4C93"];
  return {
    trace_items: Array.from({ length: 18 }, (_, index) => ({
      title: `일정 ${index + 1}`,
      day: index % 7,
      days: index < 4 ? 2 : 1,
      time: index < 4 ? `${String(7 + index).padStart(2, "0")}:00` : null,
      color: colors[index % colors.length],
    })),
    trace_todos: Array.from({ length: 8 }, (_, index) => `할 일 ${index + 1}`),
    device_time: "09:41",
    background_subject: "character_other",
    background_mood: "따뜻한 아침 창가",
    background_search_query: null,
    language: "ko",
  };
}

const MARKETING_CONTRACT = {
  minimumTraceItems: 18,
  maximumTraceItems: 22,
  minimumTraceTodos: 8,
  maximumTraceTodos: 12,
  acceptLegacy: false,
  includeStructuredMarker: false,
  includeNullSearchQuery: true,
};

test("workspace and marketing paths share one structured weekly input normalizer", () => {
  const normalized = normalizeCandidateImageInputs(weeklyImageInputs(), MARKETING_CONTRACT);

  assert.equal(normalized.trace_items.length, 18);
  assert.equal(normalized.trace_todos.length, 8);
  assert.equal(normalized.background_search_query, null);
  assert.equal("structured" in normalized.trace_items[0], false);
});

test("legacy rows remain readable at delivery but cannot be newly materialized", () => {
  const legacy = {
    ...weeklyImageInputs(),
    trace_items: [
      "07:00 Wake up",
      "09:00 Work",
      "12:00 Lunch",
      "18:00 Commute",
      "22:00 Sleep",
    ],
    trace_todos: [],
  };
  const delivered = normalizeCandidateImageInputs(legacy);
  assert.equal(delivered.trace_items[0].structured, false);
  assert.throws(
    () => normalizeCandidateImageInputs(legacy, MARKETING_CONTRACT),
    (error) => error instanceof CandidateImageInputsError,
  );
});

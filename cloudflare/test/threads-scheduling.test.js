import assert from "node:assert/strict";
import test from "node:test";

import { nextDailyGenerationAt } from "../src/hosted-workspace.js";
import { runHostedThreadsPublications } from "../src/threads/scheduling.js";

test("next slot is strictly future before, at, and after the account wall clock", () => {
  assert.equal(
    nextDailyGenerationAt("Asia/Seoul", "19:30", new Date("2026-08-31T05:00:00Z")).toISOString(),
    "2026-08-31T10:30:00.000Z",
  );
  assert.equal(
    nextDailyGenerationAt("Asia/Seoul", "19:30", new Date("2026-08-31T10:30:00Z")).toISOString(),
    "2026-09-01T10:30:00.000Z",
  );
  assert.equal(
    nextDailyGenerationAt("Asia/Seoul", "19:30", new Date("2026-08-31T11:00:00Z")).toISOString(),
    "2026-09-01T10:30:00.000Z",
  );
});

test("DST gaps advance safely and folds choose the next distinct occurrence", () => {
  assert.equal(
    nextDailyGenerationAt("America/New_York", "02:30", new Date("2026-03-08T06:00:00Z")).toISOString(),
    "2026-03-08T07:30:00.000Z",
  );
  assert.equal(
    nextDailyGenerationAt("America/New_York", "01:30", new Date("2026-11-01T05:30:00Z")).toISOString(),
    "2026-11-01T06:30:00.000Z",
  );
  assert.equal(
    nextDailyGenerationAt("America/New_York", "01:30", new Date("2026-11-01T06:30:00Z")).toISOString(),
    "2026-11-02T06:30:00.000Z",
  );
});

test("publication scheduler bounds work and isolates a failing row", async () => {
  const rows = Array.from({ length: 25 }, (_, index) => ({ publication_id: `publication-${index}` }));
  const env = {
    DB: {
      prepare(sql) {
        assert.match(sql, /LIMIT 20/u);
        return {
          bind() {
            return { async all() { return { results: rows.slice(0, 20) }; } };
          },
        };
      },
    },
  };
  const calls = [];
  const outcomes = await runHostedThreadsPublications(env, async (_env, publicationId) => {
    calls.push(publicationId);
    if (publicationId === "publication-3") {
      const error = new Error("fixture failure");
      error.code = "fixture_failed";
      throw error;
    }
  }, { now: () => Date.parse("2026-08-31T00:00:00Z") });
  assert.equal(calls.length, 20);
  assert.equal(outcomes.filter((item) => item.status === "succeeded").length, 19);
  assert.deepEqual(outcomes[3], {
    publication_id: "publication-3",
    status: "failed",
    error_code: "fixture_failed",
  });
});

import assert from "node:assert/strict";
import test from "node:test";

import {
  hasOnlineMarketingWorker,
  marketingJudgmentCapabilityMatches,
} from "../src/marketing-worker-capabilities.js";
import { D1Adapter } from "./d1-fixture.js";

function addWorker(db, { id, capabilities, doctor, lastSeenAt, state = "active" }) {
  db.sqlite.prepare(
    `INSERT INTO mac_workers
      (worker_id, display_name, pool, state, capabilities_json, doctor_json,
       last_seen_at, created_at, updated_at)
     VALUES (?, ?, 'appium', ?, ?, ?, ?, ?, ?)`,
  ).run(
    id,
    id,
    state,
    JSON.stringify(capabilities),
    JSON.stringify(doctor),
    lastSeenAt,
    lastSeenAt,
    lastSeenAt,
  );
}

test("marketing preflight requires an active recent worker with the exact tool version", async () => {
  const db = new D1Adapter();
  const now = new Date("2026-09-02T12:00:00.000Z");
  addWorker(db, {
    id: "stale-worker",
    capabilities: {
      task_kinds: "marketing_judgment",
      marketing_reasoning_ready: true,
      creative_plan_v1: true,
    },
    doctor: { ready: false },
    lastSeenAt: "2026-09-02T11:00:00.000Z",
  });
  addWorker(db, {
    id: "wrong-tool-worker",
    capabilities: {
      task_kinds: "marketing_judgment",
      marketing_reasoning_ready: true,
      shadow_strategy_v1: true,
    },
    doctor: { ready: false },
    lastSeenAt: "2026-09-02T11:59:45.000Z",
  });

  assert.equal(await hasOnlineMarketingWorker(db, "creative_plan", now), false);

  addWorker(db, {
    id: "reasoning-worker",
    capabilities: {
      task_kinds: "marketing_judgment",
      marketing_reasoning_ready: true,
      creative_plan_v1: true,
    },
    doctor: { ready: false },
    lastSeenAt: "2026-09-02T11:59:45.000Z",
  });

  assert.equal(await hasOnlineMarketingWorker(db, "creative_plan", now), true);
});

test("a globally healthy rolling-upgrade worker remains eligible for exact advertised tools", async () => {
  const db = new D1Adapter();
  const now = new Date("2026-09-02T12:00:00.000Z");
  addWorker(db, {
    id: "healthy-worker",
    capabilities: {
      task_kinds: "capture,marketing_judgment",
      shadow_strategy_v1: true,
    },
    doctor: { ready: true },
    lastSeenAt: "2026-09-02T11:59:45.000Z",
  });

  assert.equal(await hasOnlineMarketingWorker(db, "shadow_strategy", now), true);
});

test("callbacks accept their frozen tool version and only legacy null bindings", () => {
  assert.equal(marketingJudgmentCapabilityMatches({}, "creative_plan"), true);
  assert.equal(marketingJudgmentCapabilityMatches(
    { required_capability: "creative_plan_v1" },
    "creative_plan",
  ), true);
  assert.equal(marketingJudgmentCapabilityMatches(
    { required_capability: "shadow_strategy_v1" },
    "creative_plan",
  ), false);
});

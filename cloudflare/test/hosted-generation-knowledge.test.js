import assert from "node:assert/strict";
import test from "node:test";
import { D1Adapter } from "./d1-fixture.js";
import { publishCandidateGeneration } from "../src/hosted-workspace.js";
import { canonicalSha256 } from "../src/marketing-agent-runs.js";

const ACCOUNT = "trace_demo_kr";
const REGISTRY = { global: "Global", countries: { KR: "Korea" } };
const FEEDBACK_WORKER = { task_kinds: "generate_candidates", feedback_context_v1: true };

async function fixture(t, capabilities = FEEDBACK_WORKER) {
  const DB = new D1Adapter();
  t.after(() => DB.sqlite.close());
  DB.sqlite.exec(`INSERT INTO hosted_workspace_accounts
    (account_id, display_name, country, language, timezone, morning_time, evening_time,
     revision, created_at, updated_at)
    VALUES ('trace_demo_kr', 'Fixture', 'KR', 'ko', 'Asia/Seoul', '07:30', '19:30', 1, 0, 0)`);
  const now = new Date().toISOString();
  DB.sqlite.prepare(`INSERT INTO mac_workers
    (worker_id, display_name, pool, state, token_sha256, capabilities_json,
     doctor_json, created_at, updated_at, last_seen_at)
    VALUES ('worker-1', 'Mac', 'appium', 'active', 'fixture', ?, '{}', ?, ?, ?)`)
    .run(JSON.stringify(capabilities), now, now, now);
  const binding = { task_ref: "task-1", run_ref: "run-1", account_id: ACCOUNT,
    workspace_id: "workspace-1", scoped_actor_ref: "actor-1", brand_ref: "brand-1",
    action_kind: "feature_launch", invocation_ref: "invocation-1" };
  const envelope = { ...binding, transfer_id: "transfer-1", receipt: { sources: [] } };
  const knowledge = { binding, knowledge_context: envelope,
    knowledge_context_sha256: await canonicalSha256(envelope) };
  const env = { DB, KNOWLEDGE_SERVICE_URL: "https://owner.invalid",
    KNOWLEDGE_SERVICE_TOKEN: "fixture-secret", KNOWLEDGE_SERVICE_PRINCIPAL_ID: "principal-1" };
  return { DB, env, knowledge };
}

test("required generation rejects feedback-only worker before consuming cooldown or queueing", async (t) => {
  const { DB, env, knowledge } = await fixture(t);
  await assert.rejects(
    publishCandidateGeneration(env, REGISTRY, null, null, null, knowledge),
    (error) => error.status === 503,
  );
  assert.equal(DB.sqlite.prepare("SELECT count(*) AS n FROM hosted_workspace_generation_locks").get().n, 0);
  assert.equal(DB.sqlite.prepare("SELECT count(*) AS n FROM hosted_workspace_capture_tasks").get().n, 0);
  DB.sqlite.prepare("UPDATE mac_workers SET capabilities_json = ?")
    .run(JSON.stringify({ ...FEEDBACK_WORKER, knowledge_context_v1: true }));
  const accepted = await publishCandidateGeneration(env, REGISTRY, null, null, null, knowledge);
  assert.equal(accepted.state, "queued");
  assert.equal(DB.sqlite.prepare("SELECT required_capability FROM hosted_workspace_capture_tasks").get().required_capability,
    "knowledge_context_v1");
});

test("disabled generation still admits a feedback-only worker", async (t) => {
  const { DB, env } = await fixture(t);
  const accepted = await publishCandidateGeneration(env, REGISTRY, null, null);
  assert.equal(accepted.state, "queued");
  assert.equal(DB.sqlite.prepare("SELECT required_capability FROM hosted_workspace_capture_tasks").get().required_capability,
    "feedback_context_v1");
});

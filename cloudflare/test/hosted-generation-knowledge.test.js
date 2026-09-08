import assert from "node:assert/strict";
import test from "node:test";
import { D1Adapter } from "./d1-fixture.js";
import { publishCandidateGeneration } from "../src/hosted-workspace.js";
import { receiveHostedGenerationCallback } from "../src/hosted-generation-callback.js";
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

test("required callback retry succeeds without writes and revalidates authority and result", async (t) => {
  const { DB, env, knowledge } = await fixture(t, { ...FEEDBACK_WORKER, knowledge_context_v1: true });
  let authorized = true;
  const stages = [];
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    const request = JSON.parse(options.body);
    stages.push(request.stage);
    return Response.json({ ...request, status: authorized ? "accepted" : "rejected",
      valid_until: new Date(Date.now() + 60_000).toISOString() });
  });
  await publishCandidateGeneration(env, REGISTRY, null, null, null, knowledge);
  DB.sqlite.exec(`UPDATE hosted_workspace_capture_tasks
    SET worker_id = 'worker-1', lease_id = 'lease-1', execution_started_at = '2026-09-08'`);
  const readTask = () => DB.sqlite.prepare("SELECT * FROM hosted_workspace_capture_tasks").get();
  const wire = JSON.parse(readTask().task_json);
  const callback = { task_id: "task-1", run_id: "run-1", account_id: ACCOUNT,
    callback_id: "task-1:completed", kind: "generate_candidates",
    result: { status: "succeeded", output: { candidates: [],
      feedback_application_sha256: wire.payload.feedback_context_sha256,
      knowledge_context_use_receipt: { transfer_id: "transfer-1",
        knowledge_context_sha256: knowledge.knowledge_context_sha256,
        context_receipt: knowledge.knowledge_context.receipt } } } };
  const worker = { worker_id: "worker-1" };
  assert.equal((await receiveHostedGenerationCallback(env, readTask(), callback, worker)).duplicate, false);
  const committed = readTask();
  DB.sqlite.exec(`CREATE TRIGGER reject_duplicate_write BEFORE UPDATE ON hosted_workspace_capture_tasks
    BEGIN SELECT RAISE(ABORT, 'unexpected callback rewrite'); END`);
  assert.deepEqual(await receiveHostedGenerationCallback(env, readTask(), callback, worker),
    { accepted: true, duplicate: true });
  assert.deepEqual(readTask(), committed);
  assert.deepEqual(stages, ["accept_result", "accept_result"]);
  const changed = structuredClone(callback);
  changed.result.output.failures = 1;
  await assert.rejects(receiveHostedGenerationCallback(env, readTask(), changed, worker), /callback result changed/);
  await assert.rejects(receiveHostedGenerationCallback(env, readTask(),
    { ...callback, callback_id: "other:completed" }, worker), /callback_id does not match/);
  authorized = false;
  await assert.rejects(receiveHostedGenerationCallback(env, readTask(), callback, worker), /authority rejected/);
});

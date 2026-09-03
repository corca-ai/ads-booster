import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { registerHooks } from "node:module";
import test from "node:test";

import { receiveHostedFeatureLaunchRunCallback } from
  "../src/hosted-feature-launch-run-callback.js";
import { handleHostedMarketingAgent } from "../src/marketing-agent.js";
import {
  reconcileMarketingAgentDelegation,
  runDueMarketingAgentDelegations,
} from "../src/marketing-agent-delegations.js";
import { canonicalSha256 } from "../src/marketing-agent-runs.js";
import {
  deriveFeatureLaunchIntentSnapshot,
  expectedNextIntentPlannerReceipt,
} from "../src/marketing-run-intents.js";
import { D1Adapter } from "./d1-fixture.js";

const ACCOUNT = {
  account_id: "trace_kr",
  country: "KR",
  language: "ko",
  timezone: "Asia/Seoul",
};

function seed(DB) {
  const now = new Date().toISOString();
  DB.sqlite.prepare(
    `INSERT INTO hosted_workspace_accounts
      (account_id, display_name, country, language, timezone, morning_time, evening_time,
       revision, created_at, updated_at)
     VALUES (?, 'Trace KR', 'KR', 'ko', 'Asia/Seoul', '07:30', '19:30', 1, ?, ?)`,
  ).run(ACCOUNT.account_id, now, now);
  DB.sqlite.prepare(
    `INSERT INTO mac_workers
      (worker_id, display_name, pool, state, capabilities_json, doctor_json,
       last_seen_at, created_at, updated_at)
     VALUES ('worker-1', 'Mac', 'appium', 'active', ?, '{"ready":true}', ?, ?, ?)`,
  ).run(JSON.stringify({
    task_kinds: "marketing_judgment",
    marketing_reasoning_ready: true,
    feature_launch_run_v5: true,
    market_research_v1: true,
    shadow_strategy_v1: true,
  }), now, now, now);
}

async function leasedMarketingRun(launch = launchRequest()) {
  const DB = new D1Adapter();
  seed(DB);
  const env = { DB, CONTROL_PLANE_TOKEN: "secret", MARKETING_AGENT_MODEL: "gpt-test" };
  const created = await handleHostedMarketingAgent(request(launch), env, ACCOUNT);
  const taskId = (await created.json()).task.task_id;
  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-1', execution_started_at = ?
     WHERE task_id = ?`,
  ).run(new Date().toISOString(), taskId);
  const task = DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(taskId);
  return { DB, env, task };
}

function featurePacket() {
  return {
    schema_version: "trace.feature-evidence.v1",
    packet_id: "packet-run-1",
    feature_id: "trace.lockscreen.ai-concepts",
    title: "AI 잠금화면 컨셉 정하기",
    lifecycle: "source_candidate",
    repository: "corca-ai/trace",
    mutable_ref: "develop",
    resolved_commit_sha: "a".repeat(40),
    tree_sha: "b".repeat(40),
    claims: [{
      claim_id: "claim-concept",
      text: "A character can appear in scheduled lock-screen scenes.",
      status: "source_supported",
      evidence_ids: ["source-diff"],
    }],
    evidence: [{
      evidence_id: "source-diff",
      kind: "source_diff",
      source_uri: "repo://corca-ai/trace",
      immutable_ref: "a".repeat(40),
      content_sha256: "c".repeat(64),
      result: "observed",
      collected_at: "2026-09-03T00:00:00Z",
    }],
    limitations: ["Installed behavior is not yet proven."],
    gate: {
      publication_allowed: false,
      allowed_claim_ids: [],
      blocked_claim_ids: ["claim-concept"],
      reasons: ["Fresh installed proof is required."],
    },
    observed_at: "2026-09-03T00:00:00Z",
  };
}

function launchRequest(overrides = {}) {
  const businessOutcome = "Increase qualified setup intent.";
  const currentControl = "아이폰 쓰는 유저들";
  return {
    schema_version: "trace.feature-launch-run-request.v1",
    agent_run_id: "agent-run-one",
    research: {
      schema_version: "trace.dynamic-evidence-research-request.v1",
      session_id: "research-run-one",
      account_id: ACCOUNT.account_id,
      feature_packet: featurePacket(),
      required_scopes: ["product_truth", "market_evidence"],
      marketing_context: null,
      market_context: {
        schema_version: "trace.dynamic-market-research-context.v1",
        country: "KR",
        language: "ko",
        business_outcome: businessOutcome,
        current_control: currentControl,
        query_budget: 4,
      },
      max_tool_calls: 2,
      max_cost_units: 8,
    },
    business_outcome: businessOutcome,
    current_control: currentControl,
    marketing_context_snapshot_id: null,
    ...overrides,
  };
}

function launchRequestWithCustomer() {
  const value = launchRequest();
  value.research.required_scopes = ["product_truth", "customer_intelligence", "market_evidence"];
  value.research.max_tool_calls = 3;
  return value;
}

function request(body, { method = "POST", token = "secret", path = "/runs" } = {}) {
  return new Request(`https://workspace.example/api/marketing-agent${path}`, {
    method,
    headers: token ? {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
    } : undefined,
    body: method === "POST" ? JSON.stringify(body) : undefined,
  });
}

function sqliteDatabaseAdapter(sqlite) {
  return {
    prepare(sql) {
      return {
        bind(...values) {
          return {
            async first() {
              return sqlite.prepare(sql).get(...values) ?? null;
            },
          };
        },
      };
    },
  };
}

test("one authenticated run intake is durable and exact replays do not enqueue twice", async () => {
  const DB = new D1Adapter();
  seed(DB);
  const env = { DB, CONTROL_PLANE_TOKEN: "secret", MARKETING_AGENT_MODEL: "gpt-test" };

  const first = await handleHostedMarketingAgent(request(launchRequest()), env, ACCOUNT);
  const replay = await handleHostedMarketingAgent(request(launchRequest()), env, ACCOUNT);
  const changed = launchRequest({ business_outcome: "Changed outcome" });
  changed.research.market_context.business_outcome = "Changed outcome";
  const drift = await handleHostedMarketingAgent(
    request(changed),
    env,
    ACCOUNT,
  );

  assert.equal(first.status, 202);
  assert.equal(replay.status, 202);
  assert.equal(drift.status, 409);
  const firstBody = await first.json();
  const replayBody = await replay.json();
  assert.deepEqual(replayBody, firstBody);
  assert.equal(firstBody.state, "queued");
  assert.equal(firstBody.receipt_count, 0);
  assert.equal(firstBody.capability_snapshot_sha256.length, 64);
  const pendingJourneyResponse = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one/journey" }),
    env,
    ACCOUNT,
  );
  const pendingJourney = await pendingJourneyResponse.json();
  assert.equal(pendingJourney.integrity_state, "launch_pending");
  assert.deepEqual(pendingJourney.nodes, []);
  assert.equal(pendingJourney.truncated, false);
  assert.equal(DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_marketing_agent_runs").get().count, 1);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_workspace_capture_tasks",
  ).get().count, 1);
  const task = DB.sqlite.prepare(
    "SELECT run_id, required_capability, task_json FROM hosted_workspace_capture_tasks",
  ).get();
  assert.notEqual(task.run_id, launchRequest().agent_run_id);
  assert.equal(task.required_capability, "feature_launch_run_v5");
  const published = JSON.parse(task.task_json);
  assert.equal(published.credential_ref, null);
  assert.equal(published.payload.pipeline, "hosted_marketing_agent_run_v5");
  assert.equal(
    published.payload.capability_snapshot.schema_version,
    "trace.research-capability-snapshot.v1",
  );
  assert.deepEqual(
    published.payload.capability_snapshot.capabilities.map(({ scope }) => scope),
    ["product_truth", "market_evidence"],
  );
  const frozen = DB.sqlite.prepare(
    `SELECT capability_snapshot_json, capability_snapshot_sha256
     FROM hosted_marketing_agent_runs`,
  ).get();
  assert.deepEqual(JSON.parse(frozen.capability_snapshot_json), published.payload.capability_snapshot);
  assert.equal(frozen.capability_snapshot_sha256, published.payload.capability_snapshot_sha256);
  assert.equal(
    frozen.capability_snapshot_sha256,
    "f1d9eb6cb816e0cf9b6e4d5f94bd13f6321846551833ccf1c01e2cc697e6c208",
  );
  assert.equal(task.task_json.includes("CONTROL_PLANE_TOKEN"), false);
});

test("0039 backfills legacy lineage and explicitly fails an unleaseable queued v4 task", async () => {
  const sqlite = new DatabaseSync(":memory:");
  const migrations = resolve(import.meta.dirname, "../migrations");
  for (const filename of readdirSync(migrations)
    .filter((name) => name.endsWith(".sql") && name < "0039_")
    .sort()) {
    sqlite.exec(readFileSync(resolve(migrations, filename), "utf8"));
  }
  seed({ sqlite });
  const now = "2026-09-03T00:00:00Z";
  const requestJson = JSON.stringify(launchRequest());
  const requestSha256 = await canonicalSha256(launchRequest());
  const legacyTaskJson = JSON.stringify({
    schema_version: "1",
    task_id: "legacy-task",
    run_id: "legacy-broker-run",
    account_id: ACCOUNT.account_id,
    kind: "marketing_judgment",
    payload: {
      pipeline: "hosted_marketing_agent_run_v4",
      judgment: "feature_launch_run",
      run_id: "legacy-run",
    },
    credential_ref: null,
  });
  sqlite.prepare(
    `INSERT INTO hosted_workspace_capture_tasks
      (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
       task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
     VALUES ('legacy-task', 'legacy-broker-run', ?, '', 1, 'legacy-task-key', ?,
             'queued', 'worker_broker', 'marketing_judgment', 'feature_launch_run_v4', ?, ?)`,
  ).run(ACCOUNT.account_id, legacyTaskJson, now, now);
  sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'legacy-lease',
         lease_expires_at = '2099-09-03T00:00:00Z', execution_started_at = ?
     WHERE task_id = 'legacy-task'`,
  ).run(now);
  sqlite.prepare(
    `INSERT INTO hosted_marketing_agent_runs
      (run_id, account_id, schema_version, request_json, request_sha256, idempotency_key,
       task_id, state, created_at, updated_at, capability_snapshot_json,
       capability_snapshot_sha256)
     VALUES ('legacy-run', ?, 'trace.feature-launch-run-request.v1', ?, ?,
             'legacy-run-key', 'legacy-task', 'queued', ?, ?, NULL, NULL)`,
  ).run(ACCOUNT.account_id, requestJson, requestSha256, now, now);

  sqlite.exec(readFileSync(resolve(migrations, "0039_marketing_agent_resume_loop.sql"), "utf8"));

  assert.deepEqual({ ...sqlite.prepare(
    `SELECT sequence, phase, root_request_sha256, request_sha256, resumable_scopes_json
     FROM hosted_marketing_agent_run_tasks WHERE task_id = 'legacy-task'`,
  ).get() }, {
    sequence: 1,
    phase: "initial",
    root_request_sha256: requestSha256,
    request_sha256: requestSha256,
    resumable_scopes_json: "[]",
  });
  assert.deepEqual({ ...sqlite.prepare(
    `SELECT state, failure_code, active_task_id, loop_state
     FROM hosted_marketing_agent_runs WHERE run_id = 'legacy-run'`,
  ).get() }, {
    state: "failed",
    failure_code: "feature_launch_resume_upgrade_required",
    active_task_id: null,
    loop_state: "failed",
  });
  assert.equal(sqlite.prepare(
    "SELECT state FROM hosted_workspace_capture_tasks WHERE task_id = 'legacy-task'",
  ).get().state, "failed");
  const task = sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = 'legacy-task'`,
  ).get();
  const callback = {
    schema_version: "1",
    callback_id: "legacy-task:completed",
    task_id: "legacy-task",
    run_id: "legacy-broker-run",
    account_id: ACCOUNT.account_id,
    kind: "marketing_judgment",
    result: {
      status: "succeeded",
      output: { ignored_migrated_v4_result: true },
      artifacts: [],
      failure_code: null,
    },
    completed_at: "2026-09-03T00:01:00Z",
  };
  const database = sqliteDatabaseAdapter(sqlite);
  registerHooks({
    resolve(specifier, context, nextResolve) {
      if (specifier === "cloudflare:workers") {
        return {
          url: "data:text/javascript,export class DurableObject {};"
            + "export class WorkflowEntrypoint {}",
          shortCircuit: true,
        };
      }
      return nextResolve(specifier, context);
    },
  });
  const { receiveCallback } = await import("../src/index.js");
  const firstAck = await receiveCallback(
    { DB: database }, callback, { worker_id: "worker-1" },
  );
  const repeatedAck = await receiveCallback(
    { DB: database }, callback, { worker_id: "worker-1" },
  );
  assert.deepEqual(firstAck, { accepted: true, duplicate: true, migrated: true });
  assert.deepEqual(repeatedAck, firstAck);
  await assert.rejects(
    () => receiveCallback(
      { DB: database },
      { ...callback, account_id: "other_kr" },
      { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );
  assert.equal(sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_campaigns",
  ).get().count, 0);
  assert.equal(sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_steps",
  ).get().count, 0);
  assert.equal(sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_receipts",
  ).get().count, 0);
  assert.deepEqual({ ...sqlite.prepare(
    `SELECT state, callback_id, result_json FROM hosted_workspace_capture_tasks
     WHERE task_id = 'legacy-task'`,
  ).get() }, { state: "failed", callback_id: null, result_json: null });
});

test("intent snapshot contains only the eligible canonical-order subsequence", async () => {
  const allSufficient = {
    findings: [{ scope: "product_truth", evidence_status: "sufficient" }],
  };
  const stopOnly = await deriveFeatureLaunchIntentSnapshot(
    "agent-run-one", allSufficient, "a".repeat(64), false, [],
  );
  const stopAndPropose = await deriveFeatureLaunchIntentSnapshot(
    "agent-run-one", allSufficient, "a".repeat(64), true, [],
  );
  assert.deepEqual(stopOnly.snapshot.intents.map(({ intent_id: id }) => id), ["stop"]);
  assert.deepEqual(stopAndPropose.snapshot.intents.map(({ intent_id: id }) => id), [
    "stop",
    "propose_shadow_strategy",
  ]);
});

test("run intake and status remain authority- and account-scoped", async () => {
  const DB = new D1Adapter();
  seed(DB);
  const env = { DB, CONTROL_PLANE_TOKEN: "secret", MARKETING_AGENT_MODEL: "gpt-test" };
  const unauthorized = await handleHostedMarketingAgent(
    request(launchRequest(), { token: null }),
    env,
    ACCOUNT,
  );
  assert.equal(unauthorized.status, 401);
  assert.equal(DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_marketing_agent_runs").get().count, 0);

  await handleHostedMarketingAgent(request(launchRequest()), env, ACCOUNT);
  const unauthorizedJourney = await handleHostedMarketingAgent(
    request(null, { method: "GET", token: null, path: "/runs/agent-run-one/journey" }),
    env,
    ACCOUNT,
  );
  assert.equal(unauthorizedJourney.status, 401);
  const foreign = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one" }),
    env,
    { ...ACCOUNT, account_id: "other_kr" },
  );
  assert.equal(foreign.status, 404);
  const foreignJourney = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one/journey" }),
    env,
    { ...ACCOUNT, account_id: "other_kr" },
  );
  assert.equal(foreignJourney.status, 404);
});

test("run intake fails before mutation without a pinned model or capable worker", async () => {
  const DB = new D1Adapter();
  seed(DB);
  const missingModel = await handleHostedMarketingAgent(
    request(launchRequest()),
    { DB, CONTROL_PLANE_TOKEN: "secret" },
    ACCOUNT,
  );
  assert.equal(missingModel.status, 503);
  DB.sqlite.prepare(
    `UPDATE mac_workers SET capabilities_json =
      '{"task_kinds":"marketing_judgment","marketing_reasoning_ready":true,"feature_launch_run_v4":true}'`,
  ).run();
  const missingWorker = await handleHostedMarketingAgent(
    request(launchRequest()),
    { DB, CONTROL_PLANE_TOKEN: "secret", MARKETING_AGENT_MODEL: "gpt-test" },
    ACCOUNT,
  );
  assert.equal(missingWorker.status, 503);
  assert.equal(DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_marketing_agent_runs").get().count, 0);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_workspace_capture_tasks",
  ).get().count, 0);
});

test("bound research callback creates one shadow campaign through the existing owner", async () => {
  const DB = new D1Adapter();
  seed(DB);
  const env = { DB, CONTROL_PLANE_TOKEN: "secret", MARKETING_AGENT_MODEL: "gpt-test" };
  const created = await handleHostedMarketingAgent(request(launchRequest()), env, ACCOUNT);
  const createdBody = await created.json();
  const task = DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(createdBody.task.task_id);
  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-1', execution_started_at = ?
     WHERE task_id = ?`,
  ).run(new Date().toISOString(), task.task_id);
  const currentTask = DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(task.task_id);
  const callback = await successfulCallback(currentTask);

  const accepted = await receiveHostedFeatureLaunchRunCallback(
    env, currentTask, callback, { worker_id: "worker-1" },
  );
  const completedTask = DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(task.task_id);
  const duplicate = await receiveHostedFeatureLaunchRunCallback(
    env, completedTask, callback, { worker_id: "worker-1" },
  );

  assert.equal(accepted.campaign_id, "agent-run-one");
  assert.equal(duplicate.duplicate, true);
  const run = DB.sqlite.prepare(
    "SELECT state, campaign_id, research_result_sha256 FROM hosted_marketing_agent_runs",
  ).get();
  assert.equal(run.state, "campaign_created");
  assert.equal(run.campaign_id, "agent-run-one");
  assert.equal(typeof run.research_result_sha256, "string");
  assert.equal(DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_marketing_campaigns").get().count, 1);
  const receipts = DB.sqlite.prepare(
    `SELECT sequence, scope, actual_cost_units
     FROM hosted_marketing_agent_run_receipts ORDER BY sequence`,
  ).all().map((row) => ({ ...row }));
  assert.deepEqual(receipts, [
    { sequence: 1, scope: "market_evidence", actual_cost_units: 3 },
    { sequence: 2, scope: "product_truth", actual_cost_units: 1 },
  ]);
  const proof = JSON.parse(DB.sqlite.prepare(
    `SELECT entry_json FROM hosted_marketing_agent_run_receipts
     WHERE sequence = 1`,
  ).get().entry_json);
  assert.equal(proof.invocation.schema_version, "trace.bound-tool-invocation.v1");
  assert.equal(proof.hand_result.schema_version, "trace.dynamic-research-hand-result-proof.v1");
  assert.equal(Object.hasOwn(proof.hand_result, "source_artifact"), false);
  assert.equal(proof.hand_result.source_artifact_sha256, proof.hand_result.source_sha256);
  const status = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one" }),
    env,
    ACCOUNT,
  );
  const statusBody = await status.json();
  assert.equal(statusBody.receipt_count, 2);
  assert.equal(statusBody.capability_snapshot_sha256.length, 64);
  const journeyResponse = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one/journey" }),
    env,
    ACCOUNT,
  );
  const journey = await journeyResponse.json();
  assert.equal(journey.schema_version, "trace.marketing-agent-run-journey.v1");
  assert.equal(journey.truncated, false);
  assert.deepEqual(journey.nodes.map((node) => ({
    campaign_id: node.campaign_id,
    parent_campaign_id: node.parent_campaign_id,
    relation: node.relation,
    depth: node.depth,
  })), [{
    campaign_id: "agent-run-one",
    parent_campaign_id: null,
    relation: "launch_shadow",
    depth: 0,
  }]);
  assert.equal(journey.nodes[0].causation.sha256.length, 64);
  assert.equal(journey.nodes[0].links.campaign, "/api/marketing-agent/campaigns/agent-run-one");
  const packetRow = DB.sqlite.prepare(
    "SELECT packet_id, packet_sha256 FROM hosted_marketing_feature_packets WHERE packet_id = ?",
  ).get("packet-run-1");
  const enteredAt = "2026-09-03T00:03:00.000Z";
  for (const [campaignId, mode, originCampaignId] of [
    ["assisted-one", "assisted", "agent-run-one"],
    ["standalone-shadow", "shadow", null],
  ]) {
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_campaigns
        (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
         mode, origin_campaign_id, state, projection_revision, business_outcome,
         created_at, updated_at)
       VALUES (?, ?, ?, ?, 'agent_v1', ?, ?, 'strategy_requested', 1,
               'Increase qualified setup intent.', ?, ?)`,
    ).run(
      campaignId,
      ACCOUNT.account_id,
      packetRow.packet_id,
      packetRow.packet_sha256,
      mode,
      originCampaignId,
      enteredAt,
      enteredAt,
    );
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, 1, 0, 1, 'strategy_requested', '{}', ?, ?, NULL, ?, ?, ?, 'runtime')`,
    ).run(
      `event-${campaignId}`,
      campaignId,
      campaignId === "assisted-one" ? "d".repeat(64) : "e".repeat(64),
      `campaign:${campaignId}:create`,
      campaignId,
      enteredAt,
      enteredAt,
    );
  }
  const journeyMutationSnapshot = () => ({
    campaigns: DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_marketing_campaigns").get().count,
    events: DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_marketing_run_events").get().count,
    tasks: DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_workspace_capture_tasks").get().count,
    activations: DB.sqlite.prepare(
      "SELECT count(*) AS count FROM hosted_marketing_successor_activations",
    ).get().count,
  });
  const beforeExpandedJourney = journeyMutationSnapshot();
  const expandedJourneyResponse = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one/journey" }),
    env,
    ACCOUNT,
  );
  const expandedJourney = await expandedJourneyResponse.json();
  assert.deepEqual(journeyMutationSnapshot(), beforeExpandedJourney);
  assert.deepEqual(expandedJourney.nodes.map((node) => ({
    campaign_id: node.campaign_id,
    parent_campaign_id: node.parent_campaign_id,
    relation: node.relation,
    depth: node.depth,
  })), [
    {
      campaign_id: "agent-run-one",
      parent_campaign_id: null,
      relation: "launch_shadow",
      depth: 0,
    },
    {
      campaign_id: "assisted-one",
      parent_campaign_id: "agent-run-one",
      relation: "assisted_execution",
      depth: 1,
    },
  ]);
  for (let index = 0; index < 100; index += 1) {
    const campaignId = `fanout-${String(index).padStart(3, "0")}`;
    const childTime = new Date(Date.parse(enteredAt) + index + 1).toISOString();
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_campaigns
        (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
         mode, origin_campaign_id, state, projection_revision, business_outcome,
         created_at, updated_at)
       VALUES (?, ?, ?, ?, 'agent_v1', 'assisted', 'agent-run-one', 'strategy_requested', 1,
               'Increase qualified setup intent.', ?, ?)`,
    ).run(
      campaignId,
      ACCOUNT.account_id,
      packetRow.packet_id,
      packetRow.packet_sha256,
      childTime,
      childTime,
    );
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, 1, 0, 1, 'strategy_requested', '{}', ?, ?, NULL, ?, ?, ?, 'runtime')`,
    ).run(
      `event-${campaignId}`,
      campaignId,
      (index + 1).toString(16).padStart(64, "0"),
      `campaign:${campaignId}:create`,
      campaignId,
      childTime,
      childTime,
    );
  }
  const boundedJourneyResponse = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one/journey" }),
    env,
    ACCOUNT,
  );
  const boundedJourney = await boundedJourneyResponse.json();
  assert.equal(boundedJourney.nodes.length, 100);
  assert.equal(new Set(boundedJourney.nodes.map((node) => node.campaign_id)).size, 100);
  assert.equal(boundedJourney.truncated, true);
  assert.deepEqual(boundedJourney.limits, { max_depth: 16, max_nodes: 100 });
  const wrongAccountJourney = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one/journey" }),
    env,
    { ...ACCOUNT, account_id: "another_account" },
  );
  assert.equal(wrongAccountJourney.status, 404);
  assert.throws(() => DB.sqlite.prepare(
    "UPDATE hosted_marketing_agent_run_receipts SET actual_cost_units = 0",
  ).run(), /append-only|immutable/);
  assert.throws(() => DB.sqlite.prepare(
    "DELETE FROM hosted_marketing_agent_run_receipts",
  ).run(), /append-only/);
  const tasks = DB.sqlite.prepare(
    "SELECT run_id, required_capability FROM hosted_workspace_capture_tasks ORDER BY created_at",
  ).all();
  assert.equal(tasks.length, 2);
  assert.deepEqual(tasks.map((item) => item.required_capability), [
    "feature_launch_run_v5",
    "market_research_v1",
  ]);
  assert.equal(tasks[1].run_id, "research-agent-run-one");
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_steps",
  ).get().count, 1);
  const intentStep = DB.sqlite.prepare(
    `SELECT parent_step_sha256, state_before_sha256, research_result_sha256,
            intent_snapshot_json, intent_snapshot_sha256, decision_json, decision_sha256,
            result_json, result_sha256, step_json, step_sha256
     FROM hosted_marketing_agent_run_steps`,
  ).get();
  const productPayload = JSON.parse(task.task_json).payload;
  assert.equal(intentStep.parent_step_sha256, null);
  assert.equal(intentStep.state_before_sha256, await canonicalSha256({
    schema_version: "trace.marketing-agent-run-step-state.v1",
    run_id: "agent-run-one",
    account_id: ACCOUNT.account_id,
    task_id: task.task_id,
    state: "queued",
    loop_state: "running",
    loop_revision: 1,
    request_sha256: productPayload.request_sha256,
    capability_snapshot_sha256: productPayload.capability_snapshot_sha256,
  }));
  assert.equal(intentStep.research_result_sha256, callback.result.output.research_result_sha256);
  assert.equal(
    intentStep.intent_snapshot_sha256,
    await canonicalSha256(JSON.parse(intentStep.intent_snapshot_json)),
  );
  assert.equal(intentStep.decision_sha256, await canonicalSha256(JSON.parse(intentStep.decision_json)));
  assert.equal(intentStep.result_sha256, await canonicalSha256(JSON.parse(intentStep.result_json)));
  assert.equal(intentStep.step_sha256, await canonicalSha256(JSON.parse(intentStep.step_json)));
  assert.throws(() => DB.sqlite.prepare(
    "UPDATE hosted_marketing_agent_run_steps SET disposition = 'stopped'",
  ).run(), /immutable/);
  assert.throws(() => DB.sqlite.prepare(
    "DELETE FROM hosted_marketing_agent_run_steps",
  ).run(), /append-only/);
  assert.throws(() => DB.sqlite.prepare(
    "UPDATE hosted_marketing_agent_runs SET next_intent_sha256 = ?",
  ).run("f".repeat(64)), /immutable/);
  const researchTask = DB.sqlite.prepare(
    "SELECT task_json FROM hosted_workspace_capture_tasks WHERE run_id = 'research-agent-run-one'",
  ).get();
  const researchPayload = JSON.parse(researchTask.task_json).payload;
  assert.deepEqual(researchPayload.market_research_seed, marketProposal());
  assert.equal(
    researchPayload.market_research_seed_sha256,
    await canonicalSha256(marketProposal()),
  );
});

test("two competing identical feature callbacks commit campaign, step, and task exactly once", async () => {
  const { DB, env, task } = await leasedMarketingRun();
  const callback = await successfulCallback(task);
  const settled = await Promise.allSettled([
    receiveHostedFeatureLaunchRunCallback(env, task, callback, { worker_id: "worker-1" }),
    receiveHostedFeatureLaunchRunCallback(env, task, callback, { worker_id: "worker-1" }),
  ]);

  assert.equal(settled.some(({ status }) => status === "fulfilled"), true);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_campaigns",
  ).get().count, 1);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_steps",
  ).get().count, 1);
  assert.equal(DB.sqlite.prepare(
    `SELECT count(*) AS count FROM hosted_workspace_capture_tasks
     WHERE required_capability = 'market_research_v1'`,
  ).get().count, 1);
  assert.equal(DB.sqlite.prepare(
    `SELECT count(*) AS count FROM hosted_workspace_capture_tasks
     WHERE task_id = ? AND state = 'succeeded' AND callback_id = ?`,
  ).get(task.task_id, callback.callback_id).count, 1);
});

test("durable delegation reconciliation survives post-campaign failure without the worker", async () => {
  const { DB, env, task } = await leasedMarketingRun();
  const callback = await successfulCallback(task);
  const accepted = await receiveHostedFeatureLaunchRunCallback(
    env,
    task,
    callback,
    { worker_id: "worker-1" },
    {
      reconcileDelegation: (currentEnv, delegationId) =>
        reconcileMarketingAgentDelegation(currentEnv, delegationId, {
          afterCampaign: () => {
            throw new Error("injected failure after campaign materialization");
          },
        }),
    },
  );
  assert.deepEqual(accepted, {
    accepted: true,
    duplicate: false,
    delegation_pending: true,
  });
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_campaigns",
  ).get().count, 1);
  assert.equal(DB.sqlite.prepare(
    "SELECT state FROM hosted_marketing_agent_run_delegations",
  ).get().state, "pending");
  assert.equal(DB.sqlite.prepare(
    "SELECT state FROM hosted_workspace_capture_tasks WHERE task_id = ?",
  ).get(task.task_id).state, "succeeded");
  const pendingStatus = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one" }),
    env,
    ACCOUNT,
  );
  assert.equal((await pendingStatus.json()).state, "delegation_pending");

  const deferredAt = new Date("2026-09-03T00:03:00Z");
  const deferred = await runDueMarketingAgentDelegations(env, {
    now: deferredAt,
    reconcile: async () => {
      throw new Error("secret raw reconciliation detail");
    },
  });
  assert.deepEqual(deferred, { finalized: 0, raced: 0, invalid: 1 });
  const retry = DB.sqlite.prepare(
    `SELECT attempt_count, next_attempt_at, last_failure_code
     FROM hosted_marketing_agent_run_delegations`,
  ).get();
  assert.deepEqual({ ...retry }, {
    attempt_count: 1,
    next_attempt_at: "2026-09-03T00:03:30.000Z",
    last_failure_code: "delegation_reconcile_failed",
  });
  assert.equal(JSON.stringify(retry).includes("secret raw"), false);

  DB.sqlite.prepare(
    "UPDATE mac_workers SET state = 'draining', last_seen_at = '2020-01-01T00:00:00Z'",
  ).run();
  const reconciled = await Promise.all([
    runDueMarketingAgentDelegations(env, { now: new Date("2026-09-03T00:03:31Z") }),
    runDueMarketingAgentDelegations(env, { now: new Date("2026-09-03T00:03:31Z") }),
  ]);
  assert.equal(reconciled.reduce((sum, item) => sum + item.finalized, 0) >= 1, true);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_campaigns",
  ).get().count, 1);
  assert.equal(DB.sqlite.prepare(
    `SELECT count(*) AS count FROM hosted_workspace_capture_tasks
     WHERE required_capability = 'market_research_v1'`,
  ).get().count, 1);
  assert.equal(DB.sqlite.prepare(
    "SELECT state FROM hosted_marketing_agent_run_delegations",
  ).get().state, "finalized");
  assert.deepEqual({ ...DB.sqlite.prepare(
    `SELECT state, campaign_id, failure_code, loop_state, active_task_id
     FROM hosted_marketing_agent_runs WHERE run_id = 'agent-run-one'`,
  ).get() }, {
    state: "campaign_created",
    campaign_id: "agent-run-one",
    failure_code: null,
    loop_state: "delegated",
    active_task_id: null,
  });
  assert.throws(() => DB.sqlite.prepare(
    "UPDATE hosted_marketing_agent_run_delegations SET delegation_json = '{}'",
  ).run(), /immutable/);
  assert.throws(() => DB.sqlite.prepare(
    "DELETE FROM hosted_marketing_agent_run_delegations",
  ).run(), /append-only/);
});

test("ten failing delegation heads back off durably so the eleventh can finalize", async () => {
  const seen = [];
  const rows = Array.from({ length: 11 }, (_, index) => ({
    delegation_id: index < 10 ? `bad-${index}` : "good-11",
    state: "pending",
    attempt_count: 0,
    next_attempt_at: null,
    created_at: `2026-09-03T00:00:${String(index).padStart(2, "0")}Z`,
  }));
  const env = {
    DB: {
      prepare(sql) {
        return {
          bind(...values) {
            return {
              async all() {
                const [nowIso, limit] = values;
                return {
                  results: rows.filter((row) => row.state === "pending"
                    && (row.next_attempt_at === null || row.next_attempt_at <= nowIso))
                    .sort((left, right) => left.created_at.localeCompare(right.created_at))
                    .slice(0, limit)
                    .map((row) => ({ ...row })),
                };
              },
              async run() {
                assert.match(sql, /SET attempt_count/u);
                const [attemptCount, nextAttemptAt, _updatedAt, delegationId, priorAttempt] = values;
                const row = rows.find((item) => item.delegation_id === delegationId);
                if (!row || row.state !== "pending" || row.attempt_count !== priorAttempt) {
                  return { meta: { changes: 0 } };
                }
                row.attempt_count = attemptCount;
                row.next_attempt_at = nextAttemptAt;
                row.last_failure_code = "delegation_reconcile_failed";
                return { meta: { changes: 1 } };
              },
            };
          },
        };
      },
    },
  };
  const now = new Date("2026-09-03T01:00:00Z");
  const first = await runDueMarketingAgentDelegations(env, {
    now,
    reconcile: async (_env, delegationId) => {
      seen.push(delegationId);
      if (delegationId.startsWith("bad-")) throw new Error("raw provider detail must not persist");
      rows.find((row) => row.delegation_id === delegationId).state = "finalized";
      return { finalized: true, duplicate: false };
    },
  });
  const second = await runDueMarketingAgentDelegations(env, {
    now,
    reconcile: async (_env, delegationId) => {
      seen.push(delegationId);
      rows.find((row) => row.delegation_id === delegationId).state = "finalized";
      return { finalized: true, duplicate: false };
    },
  });
  assert.deepEqual(first, { finalized: 0, raced: 0, invalid: 10 });
  assert.deepEqual(second, { finalized: 1, raced: 0, invalid: 0 });
  assert.equal(seen.at(-1), "good-11");
  assert.equal(rows.slice(0, 10).every((row) => row.attempt_count === 1), true);
  assert.equal(rows.slice(0, 10).every(
    (row) => row.next_attempt_at === "2026-09-03T01:00:30.000Z",
  ), true);
  assert.equal(rows.slice(0, 10).every(
    (row) => row.last_failure_code === "delegation_reconcile_failed",
  ), true);
});

test("intent step ledger rejects wrong scope, sequence, parent, update, and delete", async () => {
  const { DB, task } = await leasedMarketingRun();
  const insert = ({
    accountId = ACCOUNT.account_id,
    taskId = task.task_id,
    sequence = 1,
    parentStepSha256 = null,
  } = {}) => DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_agent_run_steps
      (run_id, account_id, task_id, sequence, parent_step_sha256, step_type,
       state_before_sha256, research_result_sha256, intent_snapshot_json,
       intent_snapshot_sha256, decision_json, decision_sha256, result_json,
       result_sha256, disposition, started_at, completed_at, step_json, step_sha256)
     VALUES ('agent-run-one', ?, ?, ?, ?, 'research_intent_decision', ?, ?, '{}', ?,
             '{}', ?, '{}', ?, 'stopped', ?, ?, '{}', ?)`,
  ).run(
    accountId,
    taskId,
    sequence,
    parentStepSha256,
    "1".repeat(64),
    "2".repeat(64),
    "3".repeat(64),
    "4".repeat(64),
    "5".repeat(64),
    "2026-09-03T00:00:00Z",
    "2026-09-03T00:00:01Z",
    "7".repeat(64),
  );

  assert.throws(() => insert({ accountId: "wrong-account" }), /binding|FOREIGN KEY/);
  assert.throws(() => insert({ taskId: "wrong-task" }), /binding|FOREIGN KEY/);
  assert.throws(() => insert({ sequence: 2 }), /binding/);
  assert.throws(() => insert({ parentStepSha256: "8".repeat(64) }), /binding/);
  insert();
  assert.throws(() => DB.sqlite.prepare(
    "UPDATE hosted_marketing_agent_run_steps SET disposition = 'needs_input'",
  ).run(), /immutable/);
  assert.throws(() => DB.sqlite.prepare(
    "DELETE FROM hosted_marketing_agent_run_steps",
  ).run(), /append-only/);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_steps",
  ).get().count, 1);
});

test("failed or tampered research cannot create a campaign", async () => {
  const DB = new D1Adapter();
  seed(DB);
  const env = { DB, CONTROL_PLANE_TOKEN: "secret", MARKETING_AGENT_MODEL: "gpt-test" };
  const created = await handleHostedMarketingAgent(request(launchRequestWithCustomer()), env, ACCOUNT);
  const taskId = (await created.json()).task.task_id;
  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-1', execution_started_at = ?
     WHERE task_id = ?`,
  ).run(new Date().toISOString(), taskId);
  const task = DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(taskId);
  const tampered = await successfulCallback(task);
  tampered.result.output.research_result.model_id = "different-model";
  tampered.result.output.research_result_sha256 = await canonicalSha256(
    tampered.result.output.research_result,
  );

  await assert.rejects(
    () => receiveHostedFeatureLaunchRunCallback(
      env, task, tampered, { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );
  assert.equal(DB.sqlite.prepare(
    "SELECT state FROM hosted_marketing_agent_runs WHERE run_id = 'agent-run-one'",
  ).get().state, "queued");
  assert.equal(DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_marketing_campaigns").get().count, 0);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_delegations",
  ).get().count, 0);

  const failure = {
    ...tampered,
    result: {
      status: "failed",
      output: {},
      artifacts: [],
      failure_code: "feature_launch_research_failed",
    },
  };
  const accepted = await receiveHostedFeatureLaunchRunCallback(
    env, task, failure, { worker_id: "worker-1" },
  );
  assert.equal(accepted.accepted, true);
  assert.equal(DB.sqlite.prepare(
    "SELECT state FROM hosted_marketing_agent_runs WHERE run_id = 'agent-run-one'",
  ).get().state, "failed");
  assert.equal(DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_marketing_campaigns").get().count, 0);
});

test("digest substitution and underreported fixed cost have zero effect", async () => {
  const DB = new D1Adapter();
  seed(DB);
  const env = { DB, CONTROL_PLANE_TOKEN: "secret", MARKETING_AGENT_MODEL: "gpt-test" };
  const created = await handleHostedMarketingAgent(request(launchRequest()), env, ACCOUNT);
  const taskId = (await created.json()).task.task_id;
  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-1', execution_started_at = ?
     WHERE task_id = ?`,
  ).run(new Date().toISOString(), taskId);
  const task = DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(taskId);
  const callback = await successfulCallback(task);
  callback.result.output.research_result.receipt_chain = forgeAllProofDigests(
    callback.result.output.research_result.receipt_chain,
  );
  callback.result.output.receipt_chain =
    structuredClone(callback.result.output.research_result.receipt_chain);
  callback.result.output.research_result_sha256 = await canonicalSha256(
    callback.result.output.research_result,
  );

  await assert.rejects(
    () => receiveHostedFeatureLaunchRunCallback(
      env, task, callback, { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );

  const underreported = await successfulCallback(task);
  const product = underreported.result.output.research_result.receipt_chain[1];
  product.actual_cost_units = 0;
  product.receipt.actual_cost_units = 0;
  product.hand_result.actual_cost_units = 0;
  product.receipt.receipt_sha256 = await canonicalSha256(product.hand_result);
  product.receipt_sha256 = product.receipt.receipt_sha256;
  product.observation.receipt_sha256 = product.receipt_sha256;
  product.observation.observation_id = `observation-${product.receipt_sha256.slice(0, 24)}`;
  product.observation_sha256 = await canonicalSha256(product.observation);
  underreported.result.output.research_result.spent_cost_units = 4;
  underreported.result.output.receipt_chain = structuredClone(
    underreported.result.output.research_result.receipt_chain,
  );
  underreported.result.output.research_result_sha256 = await canonicalSha256(
    underreported.result.output.research_result,
  );
  await assert.rejects(
    () => receiveHostedFeatureLaunchRunCallback(
      env, task, underreported, { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );
  assert.equal(DB.sqlite.prepare(
    "SELECT state FROM hosted_marketing_agent_runs WHERE run_id = 'agent-run-one'",
  ).get().state, "queued");
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_receipts",
  ).get().count, 0);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_campaigns",
  ).get().count, 0);
});

test("request-more is a needs-input terminal projection and creates no task or campaign", async () => {
  const DB = new D1Adapter();
  seed(DB);
  const env = { DB, CONTROL_PLANE_TOKEN: "secret", MARKETING_AGENT_MODEL: "gpt-test" };
  const created = await handleHostedMarketingAgent(
    request(launchRequestWithCustomer()),
    env,
    ACCOUNT,
  );
  const taskId = (await created.json()).task.task_id;
  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-1', execution_started_at = ?
     WHERE task_id = ?`,
  ).run(new Date().toISOString(), taskId);
  const task = DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(taskId);
  const callback = await successfulCallback(task);

  const accepted = await receiveHostedFeatureLaunchRunCallback(
    env, task, callback, { worker_id: "worker-1" },
  );

  assert.equal(accepted.accepted, true);
  assert.equal(accepted.campaign_id, undefined);
  const run = DB.sqlite.prepare(
    "SELECT state, failure_code, campaign_id FROM hosted_marketing_agent_runs",
  ).get();
  assert.equal(run.state, "blocked");
  assert.equal(run.failure_code, "research_more_evidence_requested");
  assert.equal(run.campaign_id, null);
  assert.equal(DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_marketing_campaigns").get().count, 0);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_delegations",
  ).get().count, 0);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_workspace_capture_tasks",
  ).get().count, 1);
  const step = DB.sqlite.prepare(
    "SELECT disposition, result_json FROM hosted_marketing_agent_run_steps",
  ).get();
  assert.equal(step.disposition, "needs_input");
  assert.deepEqual(JSON.parse(step.result_json), {
    campaign_id: null,
    disposition: "needs_input",
    effect_class: "none",
    intent_id: "request_more_evidence",
    schema_version: "trace.feature-launch-intent-step-result.v1",
    tasks_created: 0,
  });
  const status = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one" }),
    env,
    ACCOUNT,
  );
  assert.deepEqual((await status.json()).next_intent, {
    intent_id: "request_more_evidence",
    requested_scope: "customer_intelligence",
  });
});

test("customer evidence resume is CAS-bound, idempotent, and finalizes sequence two once", async () => {
  const { DB, env, task: initialTask } = await leasedMarketingRun(launchRequestWithCustomer());
  const initialCallback = await successfulCallback(initialTask);
  await receiveHostedFeatureLaunchRunCallback(
    env, initialTask, initialCallback, { worker_id: "worker-1" },
  );
  const initialRun = DB.sqlite.prepare(
    `SELECT head_step_sha256, request_sha256, active_task_id, loop_state
     FROM hosted_marketing_agent_runs WHERE run_id = 'agent-run-one'`,
  ).get();
  assert.equal(initialRun.active_task_id, null);
  assert.equal(initialRun.loop_state, "needs_input");
  const context = await seedMarketingContext(DB, "context-resume-one");
  const resumeBody = {
    schema_version: "trace.marketing-agent-resume-request.v1",
    resume_id: "resume-one",
    expected_head_step_sha256: initialRun.head_step_sha256,
    marketing_context_snapshot_id: context.snapshot_id,
  };

  const [resumed, replay] = await Promise.all([
    handleHostedMarketingAgent(
      request(resumeBody, { path: "/runs/agent-run-one/resume" }),
      env,
      ACCOUNT,
    ),
    handleHostedMarketingAgent(
      request(resumeBody, { path: "/runs/agent-run-one/resume" }),
      env,
      ACCOUNT,
    ),
  ]);
  const drift = await handleHostedMarketingAgent(
    request(
      { ...resumeBody, marketing_context_snapshot_id: "context-drift" },
      { path: "/runs/agent-run-one/resume" },
    ),
    env,
    ACCOUNT,
  );
  const stale = await handleHostedMarketingAgent(
    request(
      { ...resumeBody, resume_id: "resume-stale", expected_head_step_sha256: "f".repeat(64) },
      { path: "/runs/agent-run-one/resume" },
    ),
    env,
    ACCOUNT,
  );
  const foreign = await handleHostedMarketingAgent(
    request(resumeBody, { path: "/runs/agent-run-one/resume" }),
    env,
    { ...ACCOUNT, account_id: "other_kr" },
  );

  assert.equal(resumed.status, 202);
  assert.equal(replay.status, 202);
  assert.deepEqual(await replay.json(), await resumed.clone().json());
  assert.equal(drift.status, 409);
  assert.equal(stale.status, 409);
  assert.equal(foreign.status, 404);
  const mapping = DB.sqlite.prepare(
    `SELECT mapping.*, task.required_capability, task.task_json
     FROM hosted_marketing_agent_run_tasks AS mapping
     JOIN hosted_workspace_capture_tasks AS task ON task.task_id = mapping.task_id
     WHERE mapping.run_id = 'agent-run-one' AND mapping.sequence = 2`,
  ).get();
  const childPublished = JSON.parse(mapping.task_json);
  assert.equal(mapping.phase, "resume");
  assert.equal(mapping.parent_step_sha256, initialRun.head_step_sha256);
  assert.equal(mapping.root_request_sha256, initialRun.request_sha256);
  assert.equal(mapping.required_capability, "feature_launch_run_v5");
  assert.equal(childPublished.payload.pipeline, "hosted_marketing_agent_run_v5");
  assert.equal(childPublished.payload.step_sequence, 2);
  assert.equal(childPublished.payload.phase, "resume");
  assert.deepEqual(childPublished.payload.resumable_scopes, []);
  assert.equal(childPublished.payload.launch_request.research.session_id, "resume-resume-one");
  assert.deepEqual(childPublished.payload.launch_request.research.marketing_context, context.projection);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_tasks",
  ).get().count, 2);

  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-2', execution_started_at = ?
     WHERE task_id = ?`,
  ).run(new Date().toISOString(), mapping.task_id);
  const childTask = DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(mapping.task_id);
  const childCallback = await successfulCallback(childTask);
  assert.equal(childCallback.result.output.next_intent.intent_id, "propose_shadow_strategy");
  const settled = await Promise.allSettled([
    receiveHostedFeatureLaunchRunCallback(
      env, childTask, childCallback, { worker_id: "worker-1" },
    ),
    receiveHostedFeatureLaunchRunCallback(
      env, childTask, childCallback, { worker_id: "worker-1" },
    ),
  ]);
  assert.equal(settled.some(({ status }) => status === "fulfilled"), true);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_campaigns",
  ).get().count, 1);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_steps",
  ).get().count, 2);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_task_receipts",
  ).get().count, 3);
  const steps = DB.sqlite.prepare(
    `SELECT sequence, parent_step_sha256, step_sha256
     FROM hosted_marketing_agent_run_steps ORDER BY sequence`,
  ).all();
  assert.equal(steps[1].parent_step_sha256, steps[0].step_sha256);
  const completed = DB.sqlite.prepare(
    `SELECT state, loop_state, loop_revision, completed_steps, cumulative_cost_units,
            active_task_id, head_step_sha256
     FROM hosted_marketing_agent_runs WHERE run_id = 'agent-run-one'`,
  ).get();
  assert.deepEqual({ ...completed }, {
    state: "campaign_created",
    loop_state: "delegated",
    loop_revision: 4,
    completed_steps: 2,
    cumulative_cost_units: 10,
    active_task_id: null,
    head_step_sha256: steps[1].step_sha256,
  });
  const overBudget = await handleHostedMarketingAgent(
    request(
      { ...resumeBody, resume_id: "resume-two", expected_head_step_sha256: steps[1].step_sha256 },
      { path: "/runs/agent-run-one/resume" },
    ),
    env,
    ACCOUNT,
  );
  assert.equal(overBudget.status, 409);
});

test("a resumed stop decision terminates without a campaign or another task", async () => {
  const { DB, env, task: initialTask } = await leasedMarketingRun(launchRequestWithCustomer());
  await receiveHostedFeatureLaunchRunCallback(
    env,
    initialTask,
    await successfulCallback(initialTask),
    { worker_id: "worker-1" },
  );
  const head = DB.sqlite.prepare(
    "SELECT head_step_sha256 FROM hosted_marketing_agent_runs WHERE run_id = 'agent-run-one'",
  ).get().head_step_sha256;
  const context = await seedMarketingContext(DB, "context-resume-stop");
  const resumed = await handleHostedMarketingAgent(request({
    schema_version: "trace.marketing-agent-resume-request.v1",
    resume_id: "resume-stop",
    expected_head_step_sha256: head,
    marketing_context_snapshot_id: context.snapshot_id,
  }, { path: "/runs/agent-run-one/resume" }), env, ACCOUNT);
  const childId = (await resumed.json()).task.task_id;
  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-stop', execution_started_at = ?
     WHERE task_id = ?`,
  ).run(new Date().toISOString(), childId);
  const childTask = DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(childId);
  const callback = await successfulCallback(childTask);
  const output = callback.result.output;
  const payload = JSON.parse(childTask.task_json).payload;
  const stopped = await fixtureNextIntent(
    payload,
    payload.run_id,
    output.research_result,
    output.research_result_sha256,
    "stop",
    null,
    true,
  );
  output.intent_snapshot = stopped.snapshot;
  output.intent_snapshot_sha256 = stopped.snapshotSha256;
  output.next_intent = stopped.decision;
  output.next_intent_sha256 = stopped.decisionSha256;
  await receiveHostedFeatureLaunchRunCallback(
    env, childTask, callback, { worker_id: "worker-1" },
  );
  const run = DB.sqlite.prepare(
    `SELECT state, loop_state, failure_code, completed_steps, active_task_id
     FROM hosted_marketing_agent_runs WHERE run_id = 'agent-run-one'`,
  ).get();
  assert.deepEqual({ ...run }, {
    state: "blocked",
    loop_state: "stopped",
    failure_code: "research_stopped_by_intent",
    completed_steps: 2,
    active_task_id: null,
  });
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_campaigns",
  ).get().count, 0);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_workspace_capture_tasks",
  ).get().count, 2);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_delegations",
  ).get().count, 0);
});

test("stop is a distinct no-effect terminal intent exposed safely in run status", async () => {
  const { DB, env, task } = await leasedMarketingRun();
  const callback = await successfulCallback(task);
  const output = callback.result.output;
  const payload = JSON.parse(task.task_json).payload;
  const intent = await fixtureNextIntent(
    payload,
    payload.run_id,
    output.research_result,
    output.research_result_sha256,
    "stop",
    null,
    true,
  );
  output.intent_snapshot = intent.snapshot;
  output.intent_snapshot_sha256 = intent.snapshotSha256;
  output.next_intent = intent.decision;
  output.next_intent_sha256 = intent.decisionSha256;

  const accepted = await receiveHostedFeatureLaunchRunCallback(
    env, task, callback, { worker_id: "worker-1" },
  );
  assert.equal(accepted.accepted, true);
  assert.equal(DB.sqlite.prepare(
    "SELECT failure_code FROM hosted_marketing_agent_runs",
  ).get().failure_code, "research_stopped_by_intent");
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_campaigns",
  ).get().count, 0);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_workspace_capture_tasks",
  ).get().count, 1);
  const status = await handleHostedMarketingAgent(
    request(null, { method: "GET", path: "/runs/agent-run-one" }),
    env,
    ACCOUNT,
  );
  const body = await status.json();
  assert.deepEqual(body.next_intent, {
    intent_id: "stop",
    requested_scope: null,
  });
  assert.equal(JSON.stringify(body).includes("Fixture selected stop."), false);
  assert.equal(JSON.parse(DB.sqlite.prepare(
    "SELECT next_intent_json FROM hosted_marketing_agent_runs",
  ).get().next_intent_json).reason, "Fixture selected stop.");
  assert.equal(body.step_count, 1);
});

test("forged, unavailable, and mismatched next intents are rejected before mutation", async () => {
  const { DB, env, task } = await leasedMarketingRun();
  const forged = await successfulCallback(task);
  const forgedOutput = forged.result.output;
  forgedOutput.intent_snapshot.intents[0].owner_id = "worker-owned";
  forgedOutput.intent_snapshot_sha256 = await canonicalSha256(forgedOutput.intent_snapshot);
  forgedOutput.next_intent.intent_snapshot_sha256 = forgedOutput.intent_snapshot_sha256;
  forgedOutput.next_intent.planner_receipt = await expectedNextIntentPlannerReceipt(
    forgedOutput.next_intent.run_id,
    forgedOutput.research_result,
    forgedOutput.research_result_sha256,
    forgedOutput.intent_snapshot,
    "gpt-test",
  );
  forgedOutput.next_intent_sha256 = await canonicalSha256(forgedOutput.next_intent);
  await assert.rejects(
    () => receiveHostedFeatureLaunchRunCallback(
      env, task, forged, { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );

  const unavailable = await successfulCallback(task);
  unavailable.result.output.next_intent.intent_id = "publish_now";
  unavailable.result.output.next_intent_sha256 = await canonicalSha256(
    unavailable.result.output.next_intent,
  );
  await assert.rejects(
    () => receiveHostedFeatureLaunchRunCallback(
      env, task, unavailable, { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );

  const mismatch = await successfulCallback(task);
  mismatch.result.output.next_intent.intent_id = "request_more_evidence";
  mismatch.result.output.next_intent.requested_scope = "customer_intelligence";
  mismatch.result.output.next_intent_sha256 = await canonicalSha256(
    mismatch.result.output.next_intent,
  );
  await assert.rejects(
    () => receiveHostedFeatureLaunchRunCallback(
      env, task, mismatch, { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );

  assert.equal(DB.sqlite.prepare(
    "SELECT state FROM hosted_marketing_agent_runs",
  ).get().state, "queued");
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_steps",
  ).get().count, 0);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_agent_run_receipts",
  ).get().count, 0);
  assert.equal(DB.sqlite.prepare(
    "SELECT count(*) AS count FROM hosted_marketing_campaigns",
  ).get().count, 0);
});

test("a market proposal that drifts from its finding cannot create a campaign", async () => {
  const DB = new D1Adapter();
  seed(DB);
  const env = { DB, CONTROL_PLANE_TOKEN: "secret", MARKETING_AGENT_MODEL: "gpt-test" };
  const created = await handleHostedMarketingAgent(request(launchRequest()), env, ACCOUNT);
  const taskId = (await created.json()).task.task_id;
  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-1', execution_started_at = ?
     WHERE task_id = ?`,
  ).run(new Date().toISOString(), taskId);
  const task = DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(taskId);
  const callback = await successfulCallback(task);
  callback.result.output.research_result.market_proposal.sources[0].url =
    "https://different.example/source";
  callback.result.output.research_result_sha256 = await canonicalSha256(
    callback.result.output.research_result,
  );

  await assert.rejects(
    () => receiveHostedFeatureLaunchRunCallback(
      env, task, callback, { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );
  assert.equal(DB.sqlite.prepare("SELECT count(*) AS count FROM hosted_marketing_campaigns").get().count, 0);
});

test("a completed run rejects a changed callback instead of silently replaying it", async () => {
  const DB = new D1Adapter();
  seed(DB);
  const env = { DB, CONTROL_PLANE_TOKEN: "secret", MARKETING_AGENT_MODEL: "gpt-test" };
  const created = await handleHostedMarketingAgent(request(launchRequest()), env, ACCOUNT);
  const taskId = (await created.json()).task.task_id;
  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-1', execution_started_at = ?
     WHERE task_id = ?`,
  ).run(new Date().toISOString(), taskId);
  const selectTask = () => DB.sqlite.prepare(
    `SELECT task_id, run_id, account_id, state, callback_id, result_json, dispatch_mode,
            worker_id, lease_id, execution_started_at, kind, task_json, required_capability
     FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
  ).get(taskId);
  const callback = await successfulCallback(selectTask());
  await receiveHostedFeatureLaunchRunCallback(
    env, selectTask(), callback, { worker_id: "worker-1" },
  );
  const changed = structuredClone(callback);
  changed.result.artifacts = [{ kind: "unexpected" }];

  await assert.rejects(
    () => receiveHostedFeatureLaunchRunCallback(
      env, selectTask(), changed, { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );
});

function forgeAllProofDigests(chain) {
  const replacements = new Map();
  let index = 1;
  const replace = (value) => {
    if (Array.isArray(value)) return value.map(replace);
    if (value && typeof value === "object") {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, replace(item)]));
    }
    if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) return value;
    if (!replacements.has(value)) {
      replacements.set(value, index.toString(16).padStart(64, "0"));
      index += 1;
    }
    return replacements.get(value);
  };
  return replace(chain);
}

async function seedMarketingContext(DB, snapshotId) {
  const snapshot = {
    schema_version: "trace.marketing-context.v1",
    snapshot_id: snapshotId,
    account_id: ACCOUNT.account_id,
    brand_guardrails: ["Do not overclaim installed behavior."],
    audience_context: ["KR iPhone users evaluating lock-screen personalization."],
    channel_policy_ids: [],
    customer_signals: [],
    approved_by: "reviewer-one",
    approved_at: "2026-09-03T00:00:00Z",
    expires_at: "2099-09-03T00:00:00Z",
  };
  const snapshotSha256 = await canonicalSha256(snapshot);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_context_snapshots
      (snapshot_id, account_id, schema_version, snapshot_json, snapshot_sha256,
       approved_by, approved_at, expires_at, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).run(
    snapshotId,
    ACCOUNT.account_id,
    snapshot.schema_version,
    JSON.stringify(snapshot),
    snapshotSha256,
    snapshot.approved_by,
    snapshot.approved_at,
    snapshot.expires_at,
    snapshot.approved_at,
  );
  return {
    snapshot_id: snapshotId,
    projection: {
      schema_version: "trace.marketing-context-projection.v1",
      snapshot_id: snapshotId,
      snapshot_sha256: snapshotSha256,
      account_id: ACCOUNT.account_id,
      brand_guardrails: snapshot.brand_guardrails,
      audience_context: snapshot.audience_context,
      channel_policy_ids: snapshot.channel_policy_ids,
      customer_signals: snapshot.customer_signals,
      expires_at: snapshot.expires_at,
    },
  };
}

async function successfulCallback(task) {
  const published = JSON.parse(task.task_json);
  const launch = published.payload.launch_request;
  const hasCustomer = launch.research.required_scopes.includes("customer_intelligence");
  const hasCurrentCustomer = hasCustomer && launch.research.marketing_context !== null;
  const researchInputSha256 = await canonicalSha256(launch.research);
  const featurePacketSha256 = await canonicalSha256(launch.research.feature_packet);
  const traceSha256 = "d".repeat(64);
  const proposal = marketProposal();
  const proposalSha256 = await canonicalSha256(proposal);
  const continuation = {
    schema_version: "trace.research-continuation.v1",
    continuation_id: `continuation-${researchInputSha256.slice(0, 24)}`,
    account_id: ACCOUNT.account_id,
    feature_packet_id: launch.research.feature_packet.packet_id,
    feature_packet_sha256: featurePacketSha256,
    research_session_id: launch.research.session_id,
    research_input_sha256: researchInputSha256,
    research_trace_sha256: traceSha256,
    pending_scope: "market_evidence",
    pending_reason: "unverified_model_proposal",
    completed_scopes: launch.research.required_scopes.filter((scope) => scope !== "market_evidence"),
    created_at: "2026-09-03T00:01:00Z",
  };
  const marketFinding = {
    iteration: 1,
    scope: "market_evidence",
    evidence_status: "insufficient",
    summary: "A quarantined proposal needs hosted byte verification.",
    caveats: ["Sources are not host-verified."],
    source_ref: `quarantined-codex-search:${proposalSha256}`,
    source_sha256: proposalSha256,
    trust_state: "unverified_model_proposal",
    supported_claim_ids: [],
  };
  const productFinding = {
    iteration: hasCustomer ? 3 : 2,
    scope: "product_truth",
    evidence_status: "insufficient",
    summary: "The packet is source-bound but not installed proof.",
    caveats: ["Installed behavior is unverified."],
    source_ref: `trace-feature-packet:${launch.research.feature_packet.packet_id}`,
    source_sha256: featurePacketSha256,
    trust_state: "packet_bound",
    supported_claim_ids: [],
  };
  const customerFinding = {
    iteration: 2,
    scope: "customer_intelligence",
    evidence_status: launch.research.marketing_context ? "sufficient" : "insufficient",
    summary: launch.research.marketing_context
      ? "Current caller-supplied customer intelligence is available."
      : "No current caller-supplied customer-intelligence projection is available.",
    caveats: ["Customer language and demand must not be invented."],
    source_ref: launch.research.marketing_context
      ? `trace-marketing-context:${launch.research.marketing_context.snapshot_id}`
      : "missing:caller-supplied-customer-intelligence",
    source_sha256: launch.research.marketing_context?.snapshot_sha256 ?? "0".repeat(64),
    trust_state: "caller_supplied_projection",
    supported_claim_ids: [],
  };
  const findings = [marketFinding];
  if (hasCustomer) findings.push(customerFinding);
  findings.push(productFinding);
  const receiptChain = [];
  for (const finding of findings) {
    receiptChain.push(await researchProofEntry(
      published.payload,
      launch,
      finding,
      finding.scope === "market_evidence" ? proposalSha256 : null,
    ));
  }
  const researchResult = {
    schema_version: "trace.dynamic-evidence-research-result.v4",
    session_id: launch.research.session_id,
    state: "inconclusive",
    input_snapshot_sha256: researchInputSha256,
    capability_snapshot: published.payload.capability_snapshot,
    registry_snapshot_sha256: published.payload.capability_snapshot_sha256,
    planner_protocol_sha256: published.payload.capability_snapshot.planner_protocol_sha256,
    provider_id: "official-codex-cli",
    model_id: "gpt-test",
    trace_sha256: traceSha256,
    tool_calls: receiptChain.length,
    spent_cost_units: hasCustomer ? 5 : 4,
    receipt_chain: receiptChain,
    findings,
    evidence_brief: null,
    continuation,
    market_proposal: proposal,
  };
  const hasExactContinuation = !hasCustomer || hasCurrentCustomer;
  const researchResultSha256 = await canonicalSha256(researchResult);
  const intent = await fixtureNextIntent(
    published.payload,
    launch.agent_run_id,
    researchResult,
    researchResultSha256,
    hasExactContinuation ? "propose_shadow_strategy" : "request_more_evidence",
    hasExactContinuation ? null : "customer_intelligence",
    hasExactContinuation,
    published.payload.resumable_scopes,
  );
  return {
    schema_version: "1",
    callback_id: `${task.task_id}:completed`,
    task_id: task.task_id,
    run_id: task.run_id,
    account_id: task.account_id,
    kind: "marketing_judgment",
    result: {
      status: "succeeded",
      output: {
        pipeline: "hosted_marketing_agent_run_v5",
        judgment: "feature_launch_run",
        task_id: task.task_id,
        run_id: launch.agent_run_id,
        account_id: ACCOUNT.account_id,
        request_sha256: published.payload.request_sha256,
        capability_snapshot: published.payload.capability_snapshot,
        capability_snapshot_sha256: published.payload.capability_snapshot_sha256,
        research_input_sha256: researchInputSha256,
        research_result: researchResult,
        research_result_sha256: researchResultSha256,
        receipt_chain: receiptChain,
        intent_snapshot: intent.snapshot,
        intent_snapshot_sha256: intent.snapshotSha256,
        next_intent: intent.decision,
        next_intent_sha256: intent.decisionSha256,
        phase: published.payload.phase,
        step_sequence: published.payload.step_sequence,
        parent_step_sha256: published.payload.parent_step_sha256,
        root_request_sha256: published.payload.root_request_sha256,
        resumable_scopes: published.payload.resumable_scopes,
        effect_class: "none",
        tool_actions_created: 0,
      },
      artifacts: [],
      failure_code: null,
    },
    completed_at: "2026-09-03T00:02:00Z",
  };
}

async function fixtureNextIntent(
  payload,
  runId,
  researchResult,
  researchResultSha256,
  intentId,
  requestedScope,
  hasExactContinuation,
  resumableScopes = payload.resumable_scopes,
) {
  const derived = await deriveFeatureLaunchIntentSnapshot(
    runId,
    researchResult,
    researchResultSha256,
    hasExactContinuation,
    resumableScopes,
  );
  const decision = {
    schema_version: "trace.feature-launch-next-intent-decision.v1",
    run_id: runId,
    research_result_sha256: researchResultSha256,
    intent_snapshot_sha256: derived.sha256,
    intent_id: intentId,
    reason: `Fixture selected ${intentId}.`,
    requested_scope: requestedScope,
    planner_receipt: await expectedNextIntentPlannerReceipt(
      runId,
      researchResult,
      researchResultSha256,
      derived.snapshot,
      payload.model_id,
    ),
  };
  return {
    snapshot: derived.snapshot,
    snapshotSha256: derived.sha256,
    decision,
    decisionSha256: await canonicalSha256(decision),
  };
}

async function researchProofEntry(payload, launch, finding, sourceArtifactSha256) {
  const snapshot = payload.capability_snapshot;
  const capability = snapshot.capabilities.find(({ scope }) => scope === finding.scope);
  const goal = {
    schema_version: "trace.evidence-research-goal.v2",
    goal_id: launch.research.session_id,
    feature_packet_id: launch.research.feature_packet.packet_id,
    feature_packet_sha256: await canonicalSha256(launch.research.feature_packet),
    input_snapshot_sha256: await canonicalSha256(launch.research),
    planner_provider_id: "official-codex-cli",
    planner_model_id: payload.model_id,
    planner_protocol_sha256: snapshot.planner_protocol_sha256,
    pinned_skill_registry_sha256: payload.capability_snapshot_sha256,
    required_scopes: launch.research.required_scopes,
    max_iterations: launch.research.required_scopes.length,
  };
  const decision = {
    schema_version: "trace.evidence-research-decision.v2",
    decision_id: `decision-${finding.iteration}`,
    goal_id: goal.goal_id,
    iteration: finding.iteration,
    skill_id: snapshot.skill_id,
    skill_sha256: snapshot.skill_sha256,
    action_id: `observe.${finding.scope}`,
    scope: finding.scope,
    claim_ids: [launch.research.feature_packet.claims[0].claim_id],
    research_question: `Question ${finding.iteration}`,
    counter_evidence_question: `Counter question ${finding.iteration}`,
    planner_receipt: {
      schema_version: "trace.planner-invocation-receipt.v1",
      provider_id: goal.planner_provider_id,
      model_id: goal.planner_model_id,
      prompt_sha256: String(finding.iteration).repeat(64),
      context_sha256: String(finding.iteration + 2).repeat(64),
      output_schema_sha256: String(finding.iteration + 4).repeat(64),
      planner_protocol_sha256: goal.planner_protocol_sha256,
    },
  };
  const request = {
    schema_version: "trace.evidence-research-tool-request.v1",
    goal,
    feature_packet_sha256: goal.feature_packet_sha256,
    decision,
  };
  const requestSha256 = await canonicalSha256({
    schema_version: "trace.bound-tool-invocation.v1",
    request_schema_sha256: capability.request_schema_sha256,
    request,
  });
  const descriptorSha256 = await canonicalSha256({
    schema_version: "trace.dynamic-research-capability.v1",
    capability_id: capability.capability_id,
    owner: capability.owner_id,
    effect_class: capability.effect_class,
    request_schema_sha256: capability.request_schema_sha256,
    worst_case_cost_units: capability.worst_case_cost_units,
  });
  const call = {
    schema_version: "trace.tool-call.v1",
    call_id: `research-${goal.goal_id}-${decision.decision_id}`,
    idempotency_key: `research:${goal.goal_id}:${finding.iteration}:${decision.action_id}`,
    capability_id: capability.capability_id,
    descriptor_sha256: descriptorSha256,
    request_schema_sha256: capability.request_schema_sha256,
    input_sha256: requestSha256,
    effect_class: "observe",
  };
  const callSha256 = await canonicalSha256(call);
  const observedAt = `2026-09-03T00:0${finding.iteration}:00Z`;
  const handResult = {
    schema_version: "trace.dynamic-research-hand-result-proof.v1",
    goal_id: goal.goal_id,
    call_id: call.call_id,
    call_sha256: callSha256,
    request_sha256: requestSha256,
    feature_packet_sha256: goal.feature_packet_sha256,
    decision_sha256: await canonicalSha256(decision),
    disposition: "succeeded",
    actual_cost_units: capability.worst_case_cost_units,
    iteration: finding.iteration,
    scope: finding.scope,
    evidence_status: finding.evidence_status,
    source_ref: finding.source_ref,
    source_sha256: finding.source_sha256,
    source_artifact_sha256: sourceArtifactSha256,
    trust_state: finding.trust_state,
    supported_claim_ids: finding.supported_claim_ids,
    summary: finding.summary,
    caveats: finding.caveats,
    observed_at: observedAt,
  };
  const receiptSha256 = await canonicalSha256(handResult);
  return {
    sequence: finding.iteration,
    iteration: finding.iteration,
    action_id: decision.action_id,
    scope: finding.scope,
    call_sha256: callSha256,
    request_sha256: requestSha256,
    receipt_sha256: receiptSha256,
    observation_sha256: await canonicalSha256({
      schema_version: "trace.evidence-research-observation.v2",
      observation_id: `observation-${receiptSha256.slice(0, 24)}`,
      scope: finding.scope,
      receipt_sha256: receiptSha256,
      call_sha256: callSha256,
      request_sha256: requestSha256,
      feature_packet_sha256: goal.feature_packet_sha256,
      decision_sha256: handResult.decision_sha256,
      source_ref: finding.source_ref,
      source_sha256: finding.source_sha256,
      evidence_summary: finding.summary,
      caveats: finding.caveats,
      trust_state: finding.trust_state,
      supported_claim_ids: finding.supported_claim_ids,
      evidence_status: finding.evidence_status,
      observed_at: observedAt,
    }),
    actual_cost_units: capability.worst_case_cost_units,
    invocation: { schema_version: "trace.bound-tool-invocation.v1", call, request },
    receipt: {
      call_id: call.call_id,
      call_sha256: callSha256,
      approval_grant_sha256: null,
      disposition: "succeeded",
      actual_cost_units: capability.worst_case_cost_units,
      receipt_sha256: receiptSha256,
    },
    observation: {
      schema_version: "trace.evidence-research-observation.v2",
      observation_id: `observation-${receiptSha256.slice(0, 24)}`,
      scope: finding.scope,
      receipt_sha256: receiptSha256,
      call_sha256: callSha256,
      request_sha256: requestSha256,
      feature_packet_sha256: goal.feature_packet_sha256,
      decision_sha256: handResult.decision_sha256,
      source_ref: finding.source_ref,
      source_sha256: finding.source_sha256,
      evidence_summary: finding.summary,
      caveats: finding.caveats,
      trust_state: finding.trust_state,
      supported_claim_ids: finding.supported_claim_ids,
      evidence_status: finding.evidence_status,
      observed_at: observedAt,
    },
    hand_result: handResult,
  };
}

function marketProposal() {
  return {
    schema_version: "trace.reference-research-proposal.v1",
    sources: [{
      source_id: "source-one",
      url: "https://example.com/one",
      title: "One",
      source_type: "article",
      summary: "A current market example.",
      published_at: "2026-08-01T00:00:00Z",
      accessed_at: "2026-09-03T00:00:00Z",
    }, {
      source_id: "source-two",
      url: "https://example.org/two",
      title: "Two",
      source_type: "research",
      summary: "Independent counterevidence.",
      published_at: null,
      accessed_at: "2026-09-03T00:00:00Z",
    }],
    observations: [{
      observation_id: "observation-one",
      classification: "format_mechanic",
      statement: "A changing scene can demonstrate progression.",
      source_ids: ["source-one"],
      confidence_basis: "Observed in the cited source.",
    }, {
      observation_id: "observation-two",
      classification: "counterevidence",
      statement: "Novelty alone may not create setup intent.",
      source_ids: ["source-two"],
      confidence_basis: "Independent source provides a counterpoint.",
    }],
    blind_spots: ["Threads-specific performance is not proven."],
  };
}

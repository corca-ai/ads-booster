import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { receiveHostedReferenceResearchCallback } from
  "../src/hosted-reference-research-callback.js";
import { createShadowCampaign, handleHostedMarketingAgent } from "../src/marketing-agent.js";
import { D1Adapter } from "./d1-fixture.js";

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function digest(value) {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

async function verifiedSourceFetcher(url) {
  return new Response(`verified source body: ${url}`, {
    status: 200,
    headers: { "content-type": "text/html" },
  });
}

function packet() {
  return {
    schema_version: "trace.feature-evidence.v1",
    packet_id: "packet-research-1",
    feature_id: "trace.lockscreen.ai-concepts",
    title: "AI 잠금화면 컨셉 정하기",
    lifecycle: "source_candidate",
    repository: "corca-ai/trace",
    mutable_ref: "develop",
    resolved_commit_sha: "a".repeat(40),
    tree_sha: "b".repeat(40),
    claims: [{
      claim_id: "claim-concept",
      text: "A character can be used in scheduled lock-screen scenes.",
      status: "source_supported",
      evidence_ids: ["diff-1"],
    }],
    evidence: [{
      evidence_id: "diff-1",
      kind: "source_diff",
      source_uri: "repo://corca-ai/trace",
      immutable_ref: "a".repeat(40),
      content_sha256: "c".repeat(64),
      result: "observed",
      collected_at: "2026-08-31T00:00:00Z",
    }],
    limitations: ["Installed behavior is not yet proven."],
    gate: {
      publication_allowed: false,
      allowed_claim_ids: [],
      blocked_claim_ids: ["claim-concept"],
      reasons: ["source only"],
    },
    observed_at: "2026-08-31T00:00:00Z",
  };
}

function seed(db) {
  db.sqlite.prepare(
    `INSERT INTO hosted_workspace_accounts
      (account_id, display_name, country, language, timezone, morning_time, evening_time,
       revision, created_at, updated_at)
     VALUES ('trace_kr', 'Trace KR', 'KR', 'ko', 'Asia/Seoul', '07:30', '19:30', 1, 1, 1)`,
  ).run();
  db.sqlite.prepare(
    `INSERT INTO mac_workers
      (worker_id, display_name, pool, state, capabilities_json, doctor_json,
       last_seen_at, created_at, updated_at)
     VALUES ('worker-1', 'Mac', 'appium', 'active', ?, '{}', 'now', 'now', 'now')`,
  ).run(JSON.stringify({
    task_kinds: "marketing_judgment",
    marketing_reasoning_ready: true,
    market_research_v1: true,
    shadow_strategy_v1: true,
  }));
}

test("market research stays quarantined and deterministically dispatches strategy", async () => {
  const DB = new D1Adapter();
  seed(DB);
  const account = {
    account_id: "trace_kr",
    country: "KR",
    language: "ko",
    timezone: "Asia/Seoul",
  };
  const lineage = {
    schema_version: "trace.feature-launch-lineage.v1",
    agent_run_id: "campaign-research-1",
    research_session_id: "local-research-1",
    research_input_sha256: "1".repeat(64),
    research_trace_sha256: "2".repeat(64),
    research_continuation_sha256: "3".repeat(64),
  };
  const created = await createShadowCampaign({ DB }, account, {
    account_id: account.account_id,
    campaign_id: "campaign-research-1",
    business_outcome: "Increase completed lock-screen setups.",
    current_control: "아이폰 쓰는 유저들...",
    feature_packet: packet(),
    agent_run_lineage: lineage,
  });
  assert.equal(created.stage, "market_research");
  const statusResponse = await handleHostedMarketingAgent(
    new Request(`https://control.example/api/marketing-agent/campaigns/${created.campaign_id}`),
    { DB },
    account,
  );
  assert.equal(statusResponse.status, 200);
  const status = await statusResponse.json();
  assert.equal(status.account_id, account.account_id);
  assert.deepEqual(status.agent_run_lineage, lineage);
  assert.equal(status.latest_evaluation, null);
  assert.equal(status.latest_learning_candidate, null);
  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-1', execution_started_at = 'now'
     WHERE task_id = ?`,
  ).run(created.task_id);
  const task = DB.sqlite.prepare(
    "SELECT * FROM hosted_workspace_capture_tasks WHERE task_id = ?",
  ).get(created.task_id);
  assert.equal(task.required_capability, "market_research_v1");
  const snapshot = {
    schema_version: "trace.reference-research.v1",
    snapshot_id: "snapshot-1",
    campaign_id: created.campaign_id,
    feature_packet_sha256: created.feature_packet_sha256,
    sources: [
      {
        source_id: "source-1",
        url: "https://example.com/one",
        title: "One",
        source_type: "article",
        summary: "Generic hooks are common.",
        published_at: null,
        accessed_at: "2026-08-31T00:00:00Z",
      },
      {
        source_id: "source-2",
        url: "https://example.org/two",
        title: "Two",
        source_type: "threads_post",
        summary: "Daily character narratives invite replies.",
        published_at: null,
        accessed_at: "2026-08-31T00:00:00Z",
      },
    ],
    observations: [
      {
        observation_id: "observation-1",
        classification: "saturation",
        statement: "The current control is saturated.",
        source_ids: ["source-1"],
        confidence_basis: "Observed repetition.",
      },
      {
        observation_id: "observation-2",
        classification: "format_mechanic",
        statement: "A day sequence creates narrative continuity.",
        source_ids: ["source-2"],
        confidence_basis: "Observed audience replies.",
      },
    ],
    blind_spots: ["No private conversion data."],
    quarantine: true,
    collected_at: "2026-08-31T00:00:00Z",
  };
  const callback = {
    callback_id: `${task.task_id}:completed`,
    task_id: task.task_id,
    run_id: task.run_id,
    account_id: task.account_id,
    kind: "marketing_judgment",
    result: {
      status: "succeeded",
      output: {
        pipeline: "hosted_marketing_judgment_v1",
        judgment: "market_research",
        campaign_id: created.campaign_id,
        reference_snapshot: snapshot,
        reference_snapshot_sha256: digest(snapshot),
        agent_run_lineage: lineage,
        tool_actions_created: 0,
      },
    },
  };

  const accepted = await receiveHostedReferenceResearchCallback(
    { DB },
    task,
    callback,
    { worker_id: "worker-1" },
    verifiedSourceFetcher,
  );

  assert.equal(accepted.state, "strategy_requested");
  const stored = DB.sqlite.prepare(
    "SELECT snapshot_json FROM hosted_marketing_reference_snapshots WHERE campaign_id = ?",
  ).get(created.campaign_id);
  assert.equal(JSON.parse(stored.snapshot_json).quarantine, true);
  const strategyTask = DB.sqlite.prepare(
    "SELECT task_json, required_capability FROM hosted_workspace_capture_tasks WHERE task_id = ?",
  ).get(accepted.strategy_task_id);
  assert.equal(strategyTask.required_capability, "shadow_strategy_v1");
  const strategyPayload = JSON.parse(strategyTask.task_json).payload;
  assert.deepEqual(strategyPayload.agent_run_lineage, lineage);
  assert.equal(strategyPayload.reference_snapshot_sha256, digest(snapshot));
  assert.equal(strategyPayload.reference_snapshot.quarantine, true);
  assert.equal(strategyPayload.reference_verification.receipts.length, 2);
  assert.equal(
    strategyPayload.reference_verification_sha256,
    digest(strategyPayload.reference_verification),
  );
  assert.equal(
    DB.sqlite.prepare(
      "SELECT COUNT(*) AS count FROM hosted_marketing_reference_source_receipts",
    ).get().count,
    2,
  );
  assert.equal(strategyPayload.feature_packet.claims.length, 1);
  assert.equal(
    DB.sqlite.prepare(
      "SELECT projection_revision FROM hosted_marketing_campaigns WHERE campaign_id = ?",
    ).get(created.campaign_id).projection_revision,
    2,
  );
});

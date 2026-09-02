import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  createShadowCampaign,
  MARKETING_JUDGMENT_PIPELINE,
  normalizeFeaturePacket,
} from "../src/marketing-agent.js";
import { receiveHostedMarketingJudgmentCallback } from
  "../src/hosted-marketing-judgment-callback.js";

const ACCOUNT = {
  account_id: "trace_kr",
  country: "KR",
  language: "ko",
  timezone: "Asia/Seoul",
};

function featurePacket(overrides = {}) {
  return {
    schema_version: "trace.feature-evidence.v1",
    packet_id: "ai-lock-screen.abc123",
    feature_id: "ai-lock-screen",
    title: "AI 잠금화면 컨셉 정하기",
    lifecycle: "source_candidate",
    repository: "corca-ai/trace",
    mutable_ref: "develop",
    resolved_commit_sha: "a".repeat(40),
    tree_sha: "b".repeat(40),
    claims: [
      {
        claim_id: "claim-concept",
        text: "선택한 캐릭터와 컨셉을 시간대별 잠금화면 생성 입력으로 사용한다.",
        status: "source_supported",
        evidence_ids: ["diff-1"],
      },
      {
        claim_id: "claim-runtime",
        text: "설치된 앱에서 일정 시간마다 잠금화면이 바뀐다.",
        status: "proposed",
        evidence_ids: [],
      },
    ],
    evidence: [{
      evidence_id: "diff-1",
      kind: "source_diff",
      source_uri: "repo://corca-ai/trace",
      immutable_ref: "a".repeat(40),
      content_sha256: "c".repeat(64),
      result: "observed",
      collected_at: "2026-08-31T00:00:00Z",
    }],
    limitations: ["설치된 앱 동작은 아직 검증하지 않았다."],
    gate: {
      publication_allowed: false,
      allowed_claim_ids: [],
      blocked_claim_ids: ["claim-runtime"],
      reasons: ["source-only evidence"],
    },
    observed_at: "2026-08-31T00:00:00Z",
    ...overrides,
  };
}

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

test("canonical integer fixture matches the Python contract digest", () => {
  assert.equal(digest({
    schema_version: "trace.experiment-evaluation.v1",
    eligible_blocks: 2,
    attribution_coverage_basis_points: 8000,
    winner_hypothesis_id: null,
    guardrail_failures: [],
  }), "573f8dbbe8c45a2fb1ae1f2b34b0d557b56a88070b344947af0d6e7a15f713d2");
});

function campaignInput(packet = featurePacket()) {
  return {
    campaign_id: "lockscreen-shadow-1",
    business_outcome: "AI 잠금화면의 차별점을 검증 가능한 Threads 포맷으로 찾는다.",
    current_control: "아이폰 쓰는 유저들...",
    feature_packet: packet,
  };
}

function creationDb({ workers = true, packetDigest = null } = {}) {
  const batches = [];
  return {
    batches,
    prepare(sql) {
      return {
        bind(...values) {
          return {
            sql,
            values,
            async all() {
              if (sql.includes("FROM mac_workers")) {
                return {
                  results: workers
                    ? [{ capabilities_json: JSON.stringify({
                      task_kinds: "capture,generate_candidates,marketing_judgment",
                    }) }]
                    : [],
                };
              }
              if (sql.includes("FROM hosted_marketing_principles")) {
                return { results: [] };
              }
              throw new Error(`unexpected all SQL: ${sql}`);
            },
            async first() {
              if (sql.includes("FROM hosted_marketing_campaigns")) return null;
              if (sql.includes("FROM hosted_marketing_feature_packets")) {
                return packetDigest ? { packet_sha256: packetDigest } : null;
              }
              throw new Error(`unexpected first SQL: ${sql}`);
            },
          };
        },
      };
    },
    async batch(statements) {
      batches.push(statements);
      return statements.map(() => ({ meta: { changes: 1 } }));
    },
  };
}

class CallbackDb {
  constructor(campaign, task) {
    this.campaign = campaign;
    this.task = task;
    this.receipts = [];
    this.briefs = [];
    this.experiments = [];
    this.hypotheses = [];
    this.arms = [];
    this.events = [];
  }

  prepare(sql) {
    const database = this;
    return {
      bind(...values) {
        const statement = { sql, values };
        return {
          ...statement,
          async first() {
            if (sql.includes("FROM hosted_marketing_context_snapshots")) return database.contextRow ?? null;
            if (sql.includes("FROM hosted_marketing_campaigns")) return database.campaign;
            throw new Error(`unexpected first SQL: ${sql}`);
          },
          async run() {
            return database.execute(statement);
          },
        };
      },
    };
  }

  async batch(statements) {
    return Promise.all(statements.map((statement) => this.execute(statement)));
  }

  async execute({ sql, values }) {
    if (sql.includes("INSERT INTO hosted_marketing_context_receipts")) {
      this.receipts.push(values);
    } else if (sql.includes("INSERT INTO hosted_marketing_strategy_briefs")) {
      this.briefs.push(values);
    } else if (sql.includes("INSERT INTO hosted_marketing_experiments")) {
      this.experiments.push(values);
    } else if (sql.includes("INSERT INTO hosted_marketing_hypotheses")) {
      this.hypotheses.push(values);
    } else if (sql.includes("INSERT INTO hosted_marketing_experiment_arms")) {
      this.arms.push(values);
    } else if (sql.includes("UPDATE hosted_marketing_campaigns")) {
      this.campaign.state = sql.includes("'failed'") ? "failed" : "experiment_registered";
      this.campaign.projection_revision = values[0];
    } else if (sql.includes("INSERT INTO hosted_marketing_run_events")) {
      this.events.push(values);
    } else if (sql.includes("UPDATE hosted_workspace_capture_tasks")) {
      [this.task.state, this.task.result_json, this.task.callback_id] = values;
    } else {
      throw new Error(`unexpected batch SQL: ${sql}`);
    }
    return { meta: { changes: 1 } };
  }
}

function contextBoundJudgmentFixture() {
  const fixture = judgmentFixture();
  const signal = {
    schema_version: "trace.customer-signal-projection.v1",
    signal_id: "signal-character-routine",
    signal_sha256: "9".repeat(64),
    audience_segment_id: "ios-character-fans",
    kind: "desired_outcome",
    summary: "A familiar character can make daily planning feel personal.",
    caveats: ["Small qualitative sample."],
    confidence_basis_points: 6500,
    observed_at: "2026-08-31T00:00:00Z",
    fresh_until: "2026-12-31T00:00:00Z",
  };
  const snapshot = {
    schema_version: "trace.marketing-context.v1",
    snapshot_id: "context-trace-kr-1",
    account_id: fixture.campaign.account_id,
    brand_guardrails: ["Lead with verified product proof."],
    audience_context: ["iPhone users who personalize a lock screen"],
    channel_policy_ids: ["threads-organic"],
    customer_signals: [signal],
    approved_by: "reviewer-1",
    approved_at: "2026-08-31T00:00:00Z",
    expires_at: "2026-12-31T00:00:00Z",
  };
  const projection = {
    schema_version: "trace.marketing-context-projection.v1",
    snapshot_id: snapshot.snapshot_id,
    snapshot_sha256: digest(snapshot),
    account_id: snapshot.account_id,
    brand_guardrails: snapshot.brand_guardrails,
    audience_context: snapshot.audience_context,
    channel_policy_ids: snapshot.channel_policy_ids,
    customer_signals: snapshot.customer_signals,
    expires_at: snapshot.expires_at,
  };
  fixture.campaign.marketing_context_snapshot_id = snapshot.snapshot_id;
  fixture.campaign.marketing_context_snapshot_sha256 = projection.snapshot_sha256;
  const payload = JSON.parse(fixture.task.task_json);
  payload.payload.marketing_context = projection;
  fixture.task.task_json = JSON.stringify(payload);
  fixture.receipt.marketing_context = projection;
  fixture.brief.context_receipt_sha256 = digest(fixture.receipt);
  fixture.result.output.context_receipt_sha256 = digest(fixture.receipt);
  fixture.result.output.strategy_brief_sha256 = digest(fixture.brief);
  return {
    ...fixture,
    contextRow: {
      snapshot_json: JSON.stringify(snapshot),
      snapshot_sha256: projection.snapshot_sha256,
      expires_at: snapshot.expires_at,
    },
  };
}

function judgmentFixture() {
  const packet = normalizeFeaturePacket(featurePacket());
  const task = {
    task_id: "judgment-task-1",
    run_id: "lockscreen-shadow-1",
    account_id: ACCOUNT.account_id,
    dispatch_mode: "legacy_queue",
    callback_id: null,
    result_json: null,
    task_json: JSON.stringify({
      payload: {
        pipeline: MARKETING_JUDGMENT_PIPELINE,
        judgment: "shadow_strategy",
        campaign_id: "lockscreen-shadow-1",
        business_outcome: campaignInput().business_outcome,
        feature_packet: packet,
        knowledge_snapshot_sha256: "d".repeat(64),
        capability_snapshot_sha256: "e".repeat(64),
      },
    }),
  };
  const campaign = {
    campaign_id: task.run_id,
    account_id: task.account_id,
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: digest(packet),
    mode: "shadow",
    state: "strategy_requested",
    projection_revision: 1,
    business_outcome: campaignInput().business_outcome,
  };
  const createdAt = "2026-08-31T00:00:00Z";
  const receipt = {
    schema_version: "trace.context-receipt.v1",
    receipt_id: task.task_id,
    campaign_id: task.run_id,
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: campaign.feature_packet_sha256,
    knowledge_snapshot_sha256: "d".repeat(64),
    capability_snapshot_sha256: "e".repeat(64),
    prompt_version: "trace.shadow-strategist.v1",
    prompt_sha256: "f".repeat(64),
    output_schema_version: "trace.strategy-proposal.v1",
    output_schema_sha256: "0".repeat(64),
    included_record_ids: ["claim-concept", "claim-runtime"],
    omitted_modules: ["external_references", "owned_experiment_learning"],
    created_at: createdAt,
  };
  const hypotheses = [
    {
      hypothesis_id: "control",
      role: "control",
      claim_ids: ["claim-concept"],
      value_frame: "기존 포맷",
      rationale: "현재 기준선",
      falsifier: "반응이 없다",
      proof_requirement: "제품 화면",
      conversation_motive: "공감",
      reference_ids: [],
    },
    {
      hypothesis_id: "character-time",
      role: "challenger",
      claim_ids: ["claim-concept"],
      value_frame: "캐릭터가 하루 흐름에 맞춰 산다",
      rationale: "정적 배경과의 차이를 보여준다",
      falsifier: "차이를 이해하지 못한다",
      proof_requirement: "시간대별 화면 시퀀스",
      conversation_motive: "내 캐릭터를 상상하게 한다",
      reference_ids: [],
    },
  ];
  const experiment = {
    experiment_id: "exp-1",
    manipulated_component: "value frame",
    held_constant_components: ["account", "posting slot"],
    allowed_incidental_differences: [],
    activated_hypothesis_ids: ["control", "character-time"],
    primary_outcome: {
      name: "setup_completed",
      scope: "direct_response_attribution",
      window_hours: 48,
      causal_estimand: null,
    },
    diagnostic_metrics: ["reply_rate"],
    guardrails: ["no unsupported product claim"],
    minimum_eligible_blocks: 4,
    maximum_posts: 8,
    maximum_duration_hours: 336,
    minimum_attribution_coverage_basis_points: 8000,
    stop_rules: ["claim contradiction"],
    inconclusive_when: ["insufficient blocks"],
  };
  const brief = {
    schema_version: "trace.strategy-brief.v1",
    brief_id: "brief-1",
    campaign_id: task.run_id,
    account_id: task.account_id,
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: campaign.feature_packet_sha256,
    context_receipt_sha256: digest(receipt),
    business_outcome: campaign.business_outcome,
    audience_situation: "좋아하는 캐릭터로 폰을 꾸미지만 정적 배경에 익숙한 아이폰 사용자",
    belief_to_change: "잠금화면은 장식이 아니라 하루에 반응하는 캐릭터 공간일 수 있다",
    hypotheses,
    experiment,
    created_at: createdAt,
  };
  const result = {
    status: "succeeded",
    output: {
      pipeline: MARKETING_JUDGMENT_PIPELINE,
      judgment: "shadow_strategy",
      campaign_id: task.run_id,
      context_receipt: receipt,
      context_receipt_sha256: digest(receipt),
      strategy_brief: brief,
      strategy_brief_sha256: digest(brief),
      publication_allowed: false,
    },
  };
  return { packet, task, campaign, receipt, brief, result };
}

test("feature packet normalization preserves only canonical shadow evidence", () => {
  const packet = normalizeFeaturePacket(featurePacket({
    observed_at: "2026-08-31T00:00:00.12Z",
  }));
  assert.equal(packet.observed_at, "2026-08-31T00:00:00.120000Z");
  assert.equal(packet.gate.publication_allowed, false);
  assert.throws(
    () => normalizeFeaturePacket(featurePacket({
      evidence: [{ ...featurePacket().evidence[0], kind: "marketing_opinion" }],
    })),
    /evidence 분류/,
  );
  assert.throws(
    () => normalizeFeaturePacket(featurePacket({
      gate: { publication_allowed: "false", allowed_claim_ids: [], blocked_claim_ids: [] },
    })),
    /boolean/,
  );
  assert.throws(
    () => normalizeFeaturePacket(featurePacket({ observed_at: "2026-08-31T09:00:00+09:00" })),
    /UTC ISO timestamp/,
  );
});

test("shadow campaign creates only an event and a judgment task", async () => {
  const DB = creationDb();
  const created = await createShadowCampaign({ DB }, ACCOUNT, campaignInput());
  assert.equal(created.state, "strategy_requested");
  assert.equal(created.publication_allowed, false);
  assert.equal(DB.batches.length, 1);
  const statements = DB.batches[0];
  assert.equal(statements.length, 5);
  const sql = statements.map((statement) => statement.sql).join("\n");
  assert.match(sql, /hosted_marketing_run_events/);
  assert.match(sql, /hosted_marketing_knowledge_snapshots/);
  assert.match(sql, /marketing_judgment/);
  assert.doesNotMatch(sql, /hosted_workspace_candidates/);
  assert.doesNotMatch(sql, /hosted_marketing_tool_actions/);
  assert.doesNotMatch(sql, /threads_publications/);
});

test("shadow campaign fails closed without a judgment worker or with a packet ID collision", async () => {
  await assert.rejects(
    createShadowCampaign({ DB: creationDb({ workers: false }) }, ACCOUNT, campaignInput()),
    /워커를 업데이트/,
  );
  await assert.rejects(
    createShadowCampaign(
      { DB: creationDb({ packetDigest: "9".repeat(64) }) },
      ACCOUNT,
      campaignInput(),
    ),
    /packet_id가 다른 feature evidence/,
  );
});

test("judgment callback stores a bound brief and registered experiment exactly once", async () => {
  const fixture = judgmentFixture();
  const DB = new CallbackDb(fixture.campaign, fixture.task);
  const callback = {
    callback_id: `${fixture.task.task_id}:completed`,
    task_id: fixture.task.task_id,
    run_id: fixture.task.run_id,
    account_id: fixture.task.account_id,
    kind: "marketing_judgment",
    result: fixture.result,
  };
  const accepted = await receiveHostedMarketingJudgmentCallback(
    { DB },
    fixture.task,
    callback,
  );
  assert.deepEqual(accepted, {
    accepted: true,
    duplicate: false,
    campaign_id: fixture.campaign.campaign_id,
    state: "experiment_registered",
    strategy_brief_id: fixture.brief.brief_id,
    experiment_id: "exp-1",
  });
  assert.equal(DB.receipts.length, 1);
  assert.equal(DB.briefs.length, 1);
  assert.equal(DB.experiments.length, 1);
  assert.equal(DB.hypotheses.length, 2);
  assert.equal(DB.arms.length, 2);
  assert.equal(DB.events.length, 1);
  assert.equal(DB.campaign.state, "experiment_registered");

  const duplicate = await receiveHostedMarketingJudgmentCallback(
    { DB },
    fixture.task,
    callback,
  );
  assert.deepEqual(duplicate, { accepted: true, duplicate: true });
  assert.equal(DB.briefs.length, 1);
});

test("judgment callback rejects unsupported claims before writing canonical state", async () => {
  const fixture = judgmentFixture();
  fixture.brief.hypotheses[1].claim_ids = ["claim-runtime"];
  fixture.result.output.strategy_brief_sha256 = digest(fixture.brief);
  const DB = new CallbackDb(fixture.campaign, fixture.task);
  await assert.rejects(
    receiveHostedMarketingJudgmentCallback(
      { DB },
      fixture.task,
      {
        callback_id: `${fixture.task.task_id}:completed`,
        task_id: fixture.task.task_id,
        run_id: fixture.task.run_id,
        account_id: fixture.task.account_id,
        kind: "marketing_judgment",
        result: fixture.result,
      },
    ),
    /unsupported feature claim/,
  );
  assert.equal(DB.receipts.length, 0);
  assert.equal(fixture.task.callback_id, null);
});

test("judgment callback rebinds the frozen customer context and rejects a rewritten projection", async () => {
  const fixture = contextBoundJudgmentFixture();
  const DB = new CallbackDb(fixture.campaign, fixture.task);
  DB.contextRow = fixture.contextRow;
  const callback = {
    callback_id: `${fixture.task.task_id}:completed`,
    task_id: fixture.task.task_id,
    run_id: fixture.task.run_id,
    account_id: fixture.task.account_id,
    kind: "marketing_judgment",
    result: fixture.result,
  };
  await receiveHostedMarketingJudgmentCallback({ DB }, fixture.task, callback);
  assert.equal(DB.receipts.length, 1);

  const forged = contextBoundJudgmentFixture();
  forged.receipt.marketing_context.customer_signals[0].signal_sha256 = "0".repeat(64);
  forged.brief.context_receipt_sha256 = digest(forged.receipt);
  forged.result.output.context_receipt_sha256 = digest(forged.receipt);
  forged.result.output.strategy_brief_sha256 = digest(forged.brief);
  const forgedDb = new CallbackDb(forged.campaign, forged.task);
  forgedDb.contextRow = forged.contextRow;
  await assert.rejects(
    receiveHostedMarketingJudgmentCallback(
      { DB: forgedDb },
      forged.task,
      {
        callback_id: `${forged.task.task_id}:completed`,
        task_id: forged.task.task_id,
        run_id: forged.task.run_id,
        account_id: forged.task.account_id,
        kind: "marketing_judgment",
        result: forged.result,
      },
    ),
    /strategy scope is invalid/,
  );
  assert.equal(forgedDb.receipts.length, 0);
});

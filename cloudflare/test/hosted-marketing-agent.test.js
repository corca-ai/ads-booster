import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
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
    account_id: ACCOUNT.account_id,
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
                      marketing_reasoning_ready: true,
                      market_research_v1: true,
                      shadow_strategy_v1: true,
                    }), doctor_json: "{}" }]
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
    this.exposurePlans = [];
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
            if (sql.includes("FROM hosted_marketing_reference_snapshots")) {
              return database.referenceSnapshotRow ?? null;
            }
            if (sql.includes("FROM hosted_workspace_accounts AS account")) {
              return database.accountRow ?? null;
            }
            if (sql.includes("FROM hosted_marketing_campaigns")) return database.campaign;
            throw new Error(`unexpected first SQL: ${sql}`);
          },
          async all() {
            if (sql.includes("FROM hosted_marketing_reference_source_receipts")) {
              return { results: database.referenceReceiptRows ?? [] };
            }
            throw new Error(`unexpected all SQL: ${sql}`);
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
    } else if (sql.includes("INSERT INTO hosted_marketing_experiment_exposure_plans")) {
      this.exposurePlans.push(values);
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
  fixture.brief.decision_dossier.selected_icp_id = signal.audience_segment_id;
  fixture.brief.decision_dossier.selection_basis_ids.push(signal.signal_id);
  fixture.brief.decision_dossier.evidence_dispositions.push({
    evidence_id: signal.signal_id,
    disposition: "supports",
    confidence_basis_points: signal.confidence_basis_points,
    freshness: "fresh",
    use: "test",
    reason: "The approved signal supports testing this audience segment.",
  });
  fixture.brief.decision_dossier.recommended_next_step = "design_experiment";
  fixture.brief.decision_dossier.reason = "The approved segment is specific enough for a bounded test.";
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
    decision_dossier: {
      schema_version: "trace.marketing-decision-dossier.v1",
      situation: "new_launch",
      selected_icp_id: "research_needed",
      selection_basis_ids: ["diff-1"],
      positioning: {
        category: "dynamic lock-screen companion",
        current_alternative: "one static lock-screen image",
        differentiated_mechanism: "scheduled scenes keep one character present through the day",
        proof_claim_ids: ["claim-concept"],
      },
      evidence_dispositions: [{
        evidence_id: "diff-1",
        disposition: "supports",
        confidence_basis_points: 7000,
        freshness: "unknown",
        use: "test",
        reason: "Source evidence supports the mechanism but not a validated ICP.",
      }],
      recommended_next_step: "research",
      reason: "Validate a concrete audience segment before assisted execution.",
      required_proof_ids: ["diff-1"],
    },
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

function referenceBoundJudgmentFixture() {
  const fixture = judgmentFixture();
  const snapshot = {
    schema_version: "trace.reference-research.v1",
    snapshot_id: "snapshot-bound-1",
    campaign_id: fixture.campaign.campaign_id,
    feature_packet_sha256: fixture.campaign.feature_packet_sha256,
    sources: [
      {
        source_id: "source-one",
        url: "https://example.com/one",
        title: "One",
        source_type: "article",
        summary: "Generic hooks are common.",
        published_at: null,
        accessed_at: "2026-08-31T00:00:00Z",
      },
      {
        source_id: "source-two",
        url: "https://example.org/two",
        title: "Two",
        source_type: "threads_post",
        summary: "Day sequences invite replies.",
        published_at: null,
        accessed_at: "2026-08-31T00:00:00Z",
      },
    ],
    observations: [
      {
        observation_id: "observation-one",
        classification: "saturation",
        statement: "The current control is saturated.",
        source_ids: ["source-one"],
        confidence_basis: "Observed repetition.",
      },
      {
        observation_id: "observation-two",
        classification: "counterevidence",
        statement: "A sequence can be too complicated without visual proof.",
        source_ids: ["source-two"],
        confidence_basis: "Observed comprehension objections.",
      },
    ],
    blind_spots: ["No private conversion data."],
    quarantine: true,
    collected_at: "2026-08-31T00:00:00Z",
  };
  const snapshotSha256 = digest(snapshot);
  const receipts = snapshot.sources.map((source, index) => ({
    schema_version: "trace.reference-source-receipt.v1",
    receipt_id: `source-receipt-${index + 1}`,
    source_id: source.source_id,
    requested_url: source.url,
    final_url: source.url,
    http_status: 200,
    content_type: "text/html",
    content_sha256: String(index + 1).repeat(64),
    byte_length: 100 + index,
    fetched_at: "2026-08-31T00:00:00Z",
  }));
  const verification = {
    schema_version: "trace.reference-verification.v1",
    snapshot_id: snapshot.snapshot_id,
    snapshot_sha256: snapshotSha256,
    receipts,
    verified_at: "2026-08-31T00:00:00Z",
  };
  const verificationSha256 = digest(verification);
  const published = JSON.parse(fixture.task.task_json);
  Object.assign(published.payload, {
    reference_snapshot: snapshot,
    reference_snapshot_sha256: snapshotSha256,
    reference_verification: verification,
    reference_verification_sha256: verificationSha256,
  });
  fixture.task.task_json = JSON.stringify(published);
  for (const observation of snapshot.observations) {
    fixture.brief.decision_dossier.evidence_dispositions.push({
      evidence_id: observation.observation_id,
      disposition: observation.classification === "counterevidence" ? "contradicts" : "insufficient",
      confidence_basis_points: 5000,
      freshness: "unknown",
      use: observation.classification === "counterevidence" ? "use_as_constraint" : "test",
      reason: "Quarantined market observation is retained without claiming verified freshness.",
    });
  }
  fixture.brief.context_receipt_sha256 = digest(fixture.receipt);
  fixture.result.output.context_receipt_sha256 = digest(fixture.receipt);
  fixture.result.output.strategy_brief_sha256 = digest(fixture.brief);
  return {
    ...fixture,
    snapshot,
    verification,
    referenceSnapshotRow: {
      verification_bundle_json: canonicalJson(verification),
      verification_bundle_sha256: verificationSha256,
    },
    referenceReceiptRows: receipts.map((receipt) => ({
      source_id: receipt.source_id,
      receipt_json: canonicalJson(receipt),
      receipt_sha256: digest(receipt),
    })),
  };
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
  assert.throws(
    () => normalizeFeaturePacket(featurePacket({ title: "😀".repeat(200) })),
    /title 값/,
  );
});

test("documented feature packet digest matches the Python launch contract", () => {
  const input = JSON.parse(readFileSync(
    new URL("../../docs/examples/feature-launch-shadow.json", import.meta.url),
    "utf8",
  ));

  assert.equal(
    digest(normalizeFeaturePacket(input.research.feature_packet)),
    "a1ed255fc06292f2350247f57e3f625b41cbe89bce4de45dfda2f2b34fcbc3a0",
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
  const judgmentTask = statements.find((statement) =>
    statement.sql.includes("INSERT INTO hosted_workspace_capture_tasks"));
  assert.equal(judgmentTask.values.at(-3), "market_research_v1");
  assert.doesNotMatch(sql, /hosted_workspace_candidates/);
  assert.doesNotMatch(sql, /hosted_marketing_tool_actions/);
  assert.doesNotMatch(sql, /threads_publications/);
});

test("feature launch lineage is normalized and bound into the hosted task", async () => {
  const DB = creationDb();
  const lineage = {
    schema_version: "trace.feature-launch-lineage.v1",
    agent_run_id: "launch-one",
    research_session_id: "research-one",
    research_input_sha256: "1".repeat(64),
    research_trace_sha256: "2".repeat(64),
    research_continuation_sha256: "3".repeat(64),
  };
  const created = await createShadowCampaign(
    { DB },
    ACCOUNT,
    { ...campaignInput(), campaign_id: "launch-one", agent_run_lineage: lineage },
  );
  const taskStatement = DB.batches[0].find((statement) =>
    statement.sql.includes("INSERT INTO hosted_workspace_capture_tasks"));
  const task = JSON.parse(taskStatement.values[4]);
  const campaignStatement = DB.batches[0].find((statement) =>
    statement.sql.includes("INSERT INTO hosted_marketing_campaigns"));

  assert.deepEqual(created.agent_run_lineage, lineage);
  assert.equal(created.account_id, ACCOUNT.account_id);
  assert.deepEqual(task.payload.agent_run_lineage, lineage);
  assert.deepEqual(campaignStatement.values.slice(9, 14), [
    "launch-one",
    "research-one",
    "1".repeat(64),
    "2".repeat(64),
    "3".repeat(64),
  ]);
  await assert.rejects(
    createShadowCampaign(
      { DB: creationDb() },
      ACCOUNT,
      { ...campaignInput(), agent_run_lineage: { ...lineage, injected: true } },
    ),
    /허용되지 않은 field/,
  );
  await assert.rejects(
    createShadowCampaign(
      { DB: creationDb() },
      ACCOUNT,
      {
        ...campaignInput(),
        account_id: "another-account",
        campaign_id: "launch-one",
        agent_run_lineage: lineage,
      },
    ),
    /인증 계정과 일치하지 않습니다/,
  );
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

test("strategy callback rebinds feature-launch lineage and rejects a changed worker result", async () => {
  const lineage = {
    schema_version: "trace.feature-launch-lineage.v1",
    agent_run_id: "lockscreen-shadow-1",
    research_session_id: "research-one",
    research_input_sha256: "1".repeat(64),
    research_trace_sha256: "2".repeat(64),
    research_continuation_sha256: "3".repeat(64),
  };
  const acceptedFixture = judgmentFixture();
  Object.assign(acceptedFixture.campaign, {
    agent_run_id: lineage.agent_run_id,
    research_session_id: lineage.research_session_id,
    research_input_sha256: lineage.research_input_sha256,
    research_trace_sha256: lineage.research_trace_sha256,
    research_continuation_sha256: lineage.research_continuation_sha256,
  });
  const acceptedTask = JSON.parse(acceptedFixture.task.task_json);
  acceptedTask.payload.agent_run_lineage = lineage;
  acceptedFixture.task.task_json = JSON.stringify(acceptedTask);
  acceptedFixture.result.output.agent_run_lineage = lineage;
  const acceptedDb = new CallbackDb(acceptedFixture.campaign, acceptedFixture.task);
  const callback = {
    callback_id: `${acceptedFixture.task.task_id}:completed`,
    task_id: acceptedFixture.task.task_id,
    run_id: acceptedFixture.task.run_id,
    account_id: acceptedFixture.task.account_id,
    kind: "marketing_judgment",
    result: acceptedFixture.result,
  };

  await receiveHostedMarketingJudgmentCallback({ DB: acceptedDb }, acceptedFixture.task, callback);
  assert.equal(acceptedDb.briefs.length, 1);

  const rejectedFixture = judgmentFixture();
  Object.assign(rejectedFixture.campaign, {
    agent_run_id: lineage.agent_run_id,
    research_session_id: lineage.research_session_id,
    research_input_sha256: lineage.research_input_sha256,
    research_trace_sha256: lineage.research_trace_sha256,
    research_continuation_sha256: lineage.research_continuation_sha256,
  });
  const rejectedTask = JSON.parse(rejectedFixture.task.task_json);
  rejectedTask.payload.agent_run_lineage = lineage;
  rejectedFixture.task.task_json = JSON.stringify(rejectedTask);
  rejectedFixture.result.output.agent_run_lineage = {
    ...lineage,
    research_trace_sha256: "9".repeat(64),
  };
  const rejectedDb = new CallbackDb(rejectedFixture.campaign, rejectedFixture.task);
  const rejectedCallback = { ...callback, result: rejectedFixture.result };
  await assert.rejects(
    receiveHostedMarketingJudgmentCallback(
      { DB: rejectedDb },
      rejectedFixture.task,
      rejectedCallback,
    ),
    /output binding/,
  );
  assert.equal(rejectedDb.briefs.length, 0);
});

test("causal strategy registration freezes one account and Threads exposure plan", async () => {
  const fixture = judgmentFixture();
  fixture.brief.experiment.primary_outcome = {
    name: "setup_completed",
    scope: "estimated_treatment_effect",
    window_hours: 48,
    causal_estimand: "difference in setup completion probability",
  };
  fixture.brief.experiment.allocation_method = "server_randomized_complete_blocks_v1";
  fixture.brief.experiment.causal_treatment_hypothesis_id = "character-time";
  fixture.brief.experiment.minimum_eligible_blocks = 2;
  fixture.brief.experiment.maximum_posts = 4;
  fixture.result.output.strategy_brief_sha256 = digest(fixture.brief);
  const DB = new CallbackDb(fixture.campaign, fixture.task);
  DB.accountRow = {
    account_id: fixture.campaign.account_id,
    timezone: "Asia/Seoul",
    morning_time: "07:30",
    evening_time: "19:30",
    account_revision: 7,
    profile_id: "profile-1",
    threads_user_id: "threads-1",
    username: "trace",
    profile_state: "active",
  };
  await receiveHostedMarketingJudgmentCallback({ DB }, fixture.task, {
    callback_id: `${fixture.task.task_id}:completed`,
    task_id: fixture.task.task_id,
    run_id: fixture.task.run_id,
    account_id: fixture.task.account_id,
    kind: "marketing_judgment",
    result: fixture.result,
  });
  assert.equal(DB.exposurePlans.length, 1);
  const plan = JSON.parse(DB.exposurePlans[0][9]);
  assert.equal(plan.account_revision, 7);
  assert.equal(plan.profile_id, "profile-1");
  assert.equal(plan.threads_user_id_snapshot, "threads-1");
  assert.equal(plan.timezone_snapshot, "Asia/Seoul");
  assert.equal(DB.events.length, 1);
  const event = JSON.parse(DB.events[0][6]);
  assert.equal(event.exposure_plan_sha256, DB.exposurePlans[0][10]);
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

test("judgment callback rejects a strategy that hides required evidence disposition", async () => {
  const fixture = judgmentFixture();
  fixture.brief.decision_dossier.evidence_dispositions = [];
  fixture.result.output.strategy_brief_sha256 = digest(fixture.brief);
  const DB = new CallbackDb(fixture.campaign, fixture.task);
  await assert.rejects(
    receiveHostedMarketingJudgmentCallback({ DB }, fixture.task, {
      callback_id: `${fixture.task.task_id}:completed`,
      task_id: fixture.task.task_id,
      run_id: fixture.task.run_id,
      account_id: fixture.task.account_id,
      kind: "marketing_judgment",
      result: fixture.result,
    }),
    /evidence dispositions/,
  );
  assert.equal(DB.briefs.length, 0);
  assert.equal(fixture.task.callback_id, null);
});

test("judgment callback rejects unbound proof IDs before canonical writes", async () => {
  const fixture = judgmentFixture();
  fixture.brief.decision_dossier.required_proof_ids = ["invented-proof"];
  fixture.result.output.strategy_brief_sha256 = digest(fixture.brief);
  const DB = new CallbackDb(fixture.campaign, fixture.task);
  await assert.rejects(
    receiveHostedMarketingJudgmentCallback({ DB }, fixture.task, {
      callback_id: `${fixture.task.task_id}:completed`,
      task_id: fixture.task.task_id,
      run_id: fixture.task.run_id,
      account_id: fixture.task.account_id,
      kind: "marketing_judgment",
      result: fixture.result,
    }),
    /required proof is unbound/,
  );
  assert.equal(DB.briefs.length, 0);
});

test("judgment callback mirrors the dossier reason length contract", async () => {
  const fixture = judgmentFixture();
  fixture.brief.decision_dossier.evidence_dispositions[0].reason = "x".repeat(1001);
  fixture.result.output.strategy_brief_sha256 = digest(fixture.brief);
  const DB = new CallbackDb(fixture.campaign, fixture.task);
  await assert.rejects(
    receiveHostedMarketingJudgmentCallback({ DB }, fixture.task, {
      callback_id: `${fixture.task.task_id}:completed`,
      task_id: fixture.task.task_id,
      run_id: fixture.task.run_id,
      account_id: fixture.task.account_id,
      kind: "marketing_judgment",
      result: fixture.result,
    }),
    /evidence dispositions are incomplete or unsafe/,
  );
  assert.equal(DB.briefs.length, 0);
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

test("judgment callback rebinds source receipts to immutable server provenance", async () => {
  const fixture = referenceBoundJudgmentFixture();
  const DB = new CallbackDb(fixture.campaign, fixture.task);
  DB.referenceSnapshotRow = fixture.referenceSnapshotRow;
  DB.referenceReceiptRows = fixture.referenceReceiptRows;
  const callback = {
    callback_id: `${fixture.task.task_id}:completed`,
    task_id: fixture.task.task_id,
    run_id: fixture.task.run_id,
    account_id: fixture.task.account_id,
    kind: "marketing_judgment",
    result: fixture.result,
  };
  await receiveHostedMarketingJudgmentCallback({ DB }, fixture.task, callback);
  assert.equal(DB.briefs.length, 1);

  const forged = referenceBoundJudgmentFixture();
  const forgedTask = JSON.parse(forged.task.task_json);
  forgedTask.payload.reference_verification.receipts[0].content_sha256 = "9".repeat(64);
  forgedTask.payload.reference_verification_sha256 = digest(
    forgedTask.payload.reference_verification,
  );
  forged.task.task_json = JSON.stringify(forgedTask);
  const forgedDb = new CallbackDb(forged.campaign, forged.task);
  forgedDb.referenceSnapshotRow = forged.referenceSnapshotRow;
  forgedDb.referenceReceiptRows = forged.referenceReceiptRows;
  await assert.rejects(
    receiveHostedMarketingJudgmentCallback({ DB: forgedDb }, forged.task, {
      ...callback,
      task_id: forged.task.task_id,
      run_id: forged.task.run_id,
      account_id: forged.task.account_id,
      result: forged.result,
    }),
    /not stored provenance/,
  );
  assert.equal(forgedDb.briefs.length, 0);
});

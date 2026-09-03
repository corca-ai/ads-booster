import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { receiveHostedCreativePlanCallback } from "../src/hosted-creative-plan-callback.js";
import {
  bindCandidateAssignment,
  decideMediaPlan,
  decideStrategyAndRequestCreative,
  handleHostedMarketingAgent,
  MARKETING_JUDGMENT_PIPELINE,
} from "../src/marketing-agent.js";

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

const account = {
  account_id: "trace_kr",
  country: "KR",
  language: "ko",
  timezone: "Asia/Seoul",
};

function creativeCapabilityRows() {
  return [
    ["capture.native_png", "trace.native_capture"],
    ["copy.text", "trace.marketing_copy"],
  ].map(([capability_id, owner_id]) => {
    const descriptor = {
      schema_version: "trace.adapter-capability.v1",
      capability_id,
      effect_class: "local_artifact",
      owner_id,
      request_schema_sha256: "a".repeat(64),
      receipt_schema_sha256: "b".repeat(64),
      activation_state: "active",
    };
    return {
      capability_id,
      descriptor_json: canonicalJson(descriptor),
      descriptor_sha256: digest(descriptor),
      effect_class: descriptor.effect_class,
      request_schema_sha256: descriptor.request_schema_sha256,
      receipt_schema_sha256: descriptor.receipt_schema_sha256,
      owner_id,
      enabled: 1,
      activation_state: "active",
    };
  });
}

function creativeCapabilityBindings() {
  return creativeCapabilityRows().map((row) => {
    const binding = {
      capability_id: row.capability_id,
      descriptor_sha256: row.descriptor_sha256,
      effect_class: row.effect_class,
      request_schema_sha256: row.request_schema_sha256,
      receipt_schema_sha256: row.receipt_schema_sha256,
      owner_id: row.owner_id,
    };
    return { ...binding, binding_sha256: digest(binding) };
  });
}

function featurePacket() {
  return {
    schema_version: "trace.feature-evidence.v1",
    packet_id: "packet-1",
    feature_id: "ai-lock-screen",
    title: "AI lock screen",
    lifecycle: "source_candidate",
    repository: "corca-ai/trace",
    mutable_ref: "develop",
    resolved_commit_sha: "a".repeat(40),
    tree_sha: "b".repeat(40),
    claims: [{
      claim_id: "claim-concept",
      text: "A selected character and concept are scheduled lock-screen inputs.",
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
    limitations: ["installed behavior is not verified"],
    gate: {
      publication_allowed: false,
      allowed_claim_ids: [],
      blocked_claim_ids: ["claim-concept"],
      reasons: ["source-only evidence"],
    },
    observed_at: "2026-08-31T00:00:00Z",
  };
}

function hypothesis(hypothesisId, role) {
  return {
    hypothesis_id: hypothesisId,
    role,
    claim_ids: ["claim-concept"],
    value_frame: hypothesisId,
    rationale: "Test one belief change.",
    falsifier: "Qualified conversation does not improve.",
    proof_requirement: "Show the concept without claiming installed behavior.",
    conversation_motive: "Ask which character the viewer would choose.",
    reference_ids: [],
  };
}

function strategyBrief(packet) {
  return {
    schema_version: "trace.strategy-brief.v1",
    brief_id: "brief-1",
    campaign_id: "campaign-1",
    account_id: "trace_kr",
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: digest(packet),
    context_receipt_sha256: "d".repeat(64),
    business_outcome: "Find a useful Threads format.",
    audience_situation: "An iPhone user who likes character lock screens.",
    belief_to_change: "A lock screen can respond to a character's day.",
    hypotheses: [hypothesis("control", "control"), hypothesis("challenger", "challenger")],
    experiment: {
      experiment_id: "experiment-1",
      manipulated_component: "value frame",
      held_constant_components: ["account", "posting slot"],
      allowed_incidental_differences: [],
      activated_hypothesis_ids: ["control", "challenger"],
      primary_outcome: {
        name: "setup_completed",
        scope: "direct_response_attribution",
        window_hours: 48,
        causal_estimand: null,
      },
      diagnostic_metrics: [],
      guardrails: ["unsupported claim"],
      minimum_eligible_blocks: 4,
      maximum_posts: 8,
      maximum_duration_hours: 336,
      minimum_attribution_coverage_basis_points: 8000,
      stop_rules: ["product fidelity failure"],
      inconclusive_when: ["insufficient blocks"],
    },
    created_at: "2026-08-31T00:00:00Z",
  };
}

function approvalDb({ workers = true } = {}) {
  const packet = featurePacket();
  const brief = strategyBrief(packet);
  const knowledgeSnapshot = {
    principles: ["한 게시물은 한 사람의 한 상황과 한 가지 믿음 변화에 집중한다."],
  };
  const batches = [];
  return {
    packet,
    brief,
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
                      creative_plan_v2: true,
                    }), doctor_json: "{}" }]
                  : [],
                };
              }
              if (sql.includes("FROM hosted_marketing_adapter_capabilities")) {
                return { results: creativeCapabilityRows() };
              }
              throw new Error(`unexpected all SQL: ${sql}`);
            },
            async first() {
              if (
                sql.includes("FROM hosted_marketing_approval_grants")
                && !sql.includes("JOIN hosted_marketing_experiments")
              ) return null;
              if (sql.includes("JOIN hosted_marketing_feature_packets")) {
                return {
                  campaign_id: "campaign-1",
                  account_id: account.account_id,
                  feature_packet_id: packet.packet_id,
                  feature_packet_sha256: digest(packet),
                  mode: "shadow",
                  state: "experiment_registered",
                  projection_revision: 2,
                  packet_json: canonicalJson(packet),
                  brief_id: brief.brief_id,
                  brief_json: canonicalJson(brief),
                  brief_sha256: digest(brief),
                  snapshot_json: canonicalJson(knowledgeSnapshot),
                  snapshot_sha256: digest(knowledgeSnapshot),
                };
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

function decisionDb({ mode = "assisted", publicationAllowed = 1 } = {}) {
  const batches = [];
  return {
    batches,
    prepare(sql) {
      return {
        bind(...values) {
          return {
            sql,
            values,
            async first() {
              if (
                sql.includes("FROM hosted_marketing_approval_grants")
                && !sql.includes("JOIN hosted_marketing_experiments")
              ) return null;
              if (sql.includes("JOIN hosted_marketing_media_plans AS plan")) {
                if (sql.includes("JOIN hosted_marketing_experiments")) {
                  return {
                    mode,
                    state: "creative_planned",
                    projection_revision: 5,
                    experiment_id: "experiment-1",
                    plan_id: "plan-1",
                    plan_sha256: "7".repeat(64),
                    plan_state: "approved",
                    publication_allowed: publicationAllowed,
                    treatment_hypothesis_id: "challenger",
                    revision: 2,
                    status: "awaiting_review",
                    marketing_assignment_id: null,
                    caption: "캐릭터가 시간에 맞춰 바뀌는 잠금화면",
                    hypothesis: "시간 기반 캐릭터 증거가 설정 의도를 높인다.",
                    appium_prompt: "Capture the native lock-screen schedule.",
                    image_inputs_json: "[]",
                    context_snapshot_json: null,
                    persona_id: "persona-1",
                    existing_hypothesis_id: null,
                    existing_treatment_id: null,
                    existing_block_id: null,
                  };
                }
                return {
                  campaign_id: "campaign-1",
                  state: "creative_planned",
                  projection_revision: 4,
                  plan_id: "plan-1",
                  plan_sha256: "7".repeat(64),
                  plan_state: "proposed",
                  publication_allowed: publicationAllowed,
                };
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

class CreativeCallbackDb {
  constructor(campaign, task) {
    this.campaign = campaign;
    this.task = task;
    this.receipts = [];
    this.plans = [];
    this.treatments = [];
    this.requests = [];
    this.capabilityBindings = [];
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
            if (sql.includes("FROM hosted_marketing_campaigns")) return database.campaign;
            throw new Error(`unexpected first SQL: ${sql}`);
          },
          async all() {
            if (sql.includes("FROM hosted_marketing_adapter_capabilities")) {
              return { results: creativeCapabilityRows() };
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
    } else if (sql.includes("INSERT INTO hosted_marketing_capability_bindings")) {
      this.capabilityBindings.push(values);
    } else if (sql.includes("INSERT INTO hosted_marketing_media_plans")) {
      this.plans.push(values);
    } else if (sql.includes("INSERT INTO hosted_marketing_creative_treatments")) {
      this.treatments.push(values);
    } else if (sql.includes("INSERT INTO hosted_marketing_artifact_requests")) {
      this.requests.push(values);
    } else if (sql.includes("UPDATE hosted_marketing_campaigns")) {
      this.campaign.state = sql.includes("'failed'") ? "failed" : "creative_planned";
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

function treatment(hypothesisId) {
  return {
    treatment_id: `treatment-${hypothesisId}`,
    hypothesis_id: hypothesisId,
    format: "native_sequence",
    hook: `hook ${hypothesisId}`,
    caption_direction: "Explain one product belief.",
    manipulated_component_value: hypothesisId,
    proof_narrative: "Label the sequence as a source-backed concept.",
    claim_ids: ["claim-concept"],
    artifact_requests: [
      {
        request_id: `request-${hypothesisId}`,
        capability_id: "copy.text",
        proof_kind: "copy_only",
        claim_ids: ["claim-concept"],
        instructions: "Compose a source-labeled explanation.",
      },
      {
        request_id: `capture-${hypothesisId}`,
        capability_id: "capture.native_png",
        proof_kind: "installed_native_capture",
        claim_ids: ["claim-concept"],
        instructions: "Capture the approved Trace lock-screen treatment.",
      },
    ],
  };
}

function creativeFixture() {
  const packet = featurePacket();
  const strategy = strategyBrief(packet);
  const capabilityBindings = creativeCapabilityBindings();
  const capabilities = capabilityBindings.map((binding) => binding.capability_id);
  const payload = {
    pipeline: MARKETING_JUDGMENT_PIPELINE,
    judgment: "creative_plan",
    creative_contract_version: "v2",
    campaign_id: "campaign-1",
    feature_packet: packet,
    feature_packet_sha256: digest(packet),
    strategy_brief: strategy,
    strategy_brief_sha256: digest(strategy),
    knowledge_snapshot_sha256: "e".repeat(64),
    capability_snapshot_sha256: digest({ capability_bindings: capabilityBindings }),
    available_capabilities: capabilities,
    available_formats: ["native_sequence"],
    capability_bindings: capabilityBindings,
  };
  const task = {
    task_id: "creative-task-1",
    run_id: "creative-run-1",
    account_id: account.account_id,
    dispatch_mode: "legacy_queue",
    callback_id: null,
    result_json: null,
    task_json: JSON.stringify({ payload }),
  };
  const campaign = {
    campaign_id: payload.campaign_id,
    account_id: account.account_id,
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: digest(packet),
    mode: "shadow",
    state: "experiment_registered",
    projection_revision: 3,
  };
  const receipt = {
    schema_version: "trace.context-receipt.v1",
    receipt_id: task.task_id,
    campaign_id: campaign.campaign_id,
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: campaign.feature_packet_sha256,
    knowledge_snapshot_sha256: payload.knowledge_snapshot_sha256,
    capability_snapshot_sha256: payload.capability_snapshot_sha256,
    prompt_version: "trace.proof-first-creative-planner.v1",
    prompt_sha256: "f".repeat(64),
    output_schema_version: "trace.creative-plan-proposal.v1",
    output_schema_sha256: "0".repeat(64),
    included_record_ids: [strategy.brief_id],
    omitted_modules: ["external_references", "owned_experiment_learning"],
    created_at: "2026-08-31T00:00:00Z",
  };
  const plan = {
    schema_version: "trace.media-plan.v1",
    plan_id: "plan-1",
    campaign_id: campaign.campaign_id,
    account_id: campaign.account_id,
    experiment_id: strategy.experiment.experiment_id,
    strategy_brief_sha256: payload.strategy_brief_sha256,
    context_receipt_sha256: digest(receipt),
    treatments: [treatment("control"), treatment("challenger")],
    publication_allowed: false,
    human_review_required: true,
    created_at: receipt.created_at,
  };
  const result = {
    status: "succeeded",
    output: {
      pipeline: MARKETING_JUDGMENT_PIPELINE,
      judgment: "creative_plan",
      campaign_id: campaign.campaign_id,
      context_receipt: receipt,
      context_receipt_sha256: digest(receipt),
      media_plan: plan,
      media_plan_sha256: digest(plan),
      publication_allowed: false,
      tool_actions_created: 0,
    },
  };
  return { task, campaign, plan, result };
}

test("exact strategy approval queues one proof-first judgment and no tool action", async () => {
  const DB = approvalDb();
  const result = await decideStrategyAndRequestCreative(
    { DB },
    account,
    "campaign-1",
    {
      strategy_brief_id: DB.brief.brief_id,
      strategy_brief_sha256: digest(DB.brief),
      reviewer_id: "reviewer-1",
      decision: "approved",
      projection_revision: 2,
    },
  );
  assert.equal(result.decision, "approved");
  assert.ok(result.creative_task_id);
  assert.equal(DB.batches.length, 1);
  const statements = DB.batches[0];
  assert.equal(statements.length, 4);
  const sql = statements.map((statement) => statement.sql).join("\n");
  assert.match(sql, /hosted_marketing_approval_grants/);
  assert.match(sql, /marketing_judgment/);
  const creativeTask = statements.find((statement) =>
    statement.sql.includes("INSERT INTO hosted_workspace_capture_tasks"));
  assert.equal(creativeTask.values.at(-3), "creative_plan_v2");
  assert.doesNotMatch(sql, /hosted_marketing_tool_actions/);
  assert.doesNotMatch(sql, /hosted_workspace_candidates/);
});

test("strategy approval fails closed when no creative judgment worker is available", async () => {
  const DB = approvalDb({ workers: false });
  await assert.rejects(
    decideStrategyAndRequestCreative(
      { DB },
      account,
      "campaign-1",
      {
        strategy_brief_id: DB.brief.brief_id,
        strategy_brief_sha256: digest(DB.brief),
        reviewer_id: "reviewer-1",
        decision: "approved",
        projection_revision: 2,
      },
    ),
    /creative judgment/,
  );
  assert.equal(DB.batches.length, 0);
});

test("creative callback persists plan and proof requests without executing them", async () => {
  const fixture = creativeFixture();
  const DB = new CreativeCallbackDb(fixture.campaign, fixture.task);
  const callback = {
    callback_id: `${fixture.task.task_id}:completed`,
    task_id: fixture.task.task_id,
    run_id: fixture.task.run_id,
    account_id: fixture.task.account_id,
    kind: "marketing_judgment",
    result: fixture.result,
  };
  const accepted = await receiveHostedCreativePlanCallback({ DB }, fixture.task, callback);
  assert.deepEqual(accepted, {
    accepted: true,
    duplicate: false,
    campaign_id: fixture.campaign.campaign_id,
    state: "creative_planned",
    media_plan_id: fixture.plan.plan_id,
  });
  assert.equal(DB.receipts.length, 1);
  assert.equal(DB.capabilityBindings.length, 2);
  assert.equal(DB.plans.length, 1);
  assert.equal(DB.treatments.length, 2);
  assert.equal(DB.requests.length, 4);
  assert.equal(DB.events.length, 1);
  assert.equal(DB.campaign.state, "creative_planned");

  const duplicate = await receiveHostedCreativePlanCallback({ DB }, fixture.task, callback);
  assert.deepEqual(duplicate, { accepted: true, duplicate: true });
  assert.equal(DB.plans.length, 1);
});

test("a frozen v1 creative callback drains through its legacy validator", async () => {
  const fixture = creativeFixture();
  fixture.task.required_capability = "creative_plan_v1";
  const taskEnvelope = JSON.parse(fixture.task.task_json);
  delete taskEnvelope.payload.creative_contract_version;
  delete taskEnvelope.payload.available_formats;
  fixture.task.task_json = JSON.stringify(taskEnvelope);
  for (const item of fixture.plan.treatments) item.format = "explanatory_carousel";
  fixture.result.output.media_plan_sha256 = digest(fixture.plan);
  const DB = new CreativeCallbackDb(fixture.campaign, fixture.task);

  const accepted = await receiveHostedCreativePlanCallback(
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
  );

  assert.equal(accepted.state, "creative_planned");
  assert.equal(DB.plans.length, 1);
});

test("a v2 creative task cannot omit its executable format projection", async () => {
  const fixture = creativeFixture();
  fixture.task.required_capability = "creative_plan_v2";
  const taskEnvelope = JSON.parse(fixture.task.task_json);
  delete taskEnvelope.payload.available_formats;
  fixture.task.task_json = JSON.stringify(taskEnvelope);
  const DB = new CreativeCallbackDb(fixture.campaign, fixture.task);

  await assert.rejects(
    receiveHostedCreativePlanCallback(
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
    /formats/,
  );
  assert.equal(DB.plans.length, 0);
});

test("creative callback rejects capability escape before canonical writes", async () => {
  const fixture = creativeFixture();
  fixture.plan.treatments[1].artifact_requests[0].capability_id = "publish.threads";
  fixture.result.output.media_plan_sha256 = digest(fixture.plan);
  const DB = new CreativeCallbackDb(fixture.campaign, fixture.task);
  await assert.rejects(
    receiveHostedCreativePlanCallback(
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
    /unavailable capability/,
  );
  assert.equal(DB.plans.length, 0);
  assert.equal(fixture.task.callback_id, null);
});

test("creative callback rejects a format without an executable adapter", async () => {
  const fixture = creativeFixture();
  fixture.plan.treatments[1].format = "screen_recording";
  fixture.result.output.media_plan_sha256 = digest(fixture.plan);
  const DB = new CreativeCallbackDb(fixture.campaign, fixture.task);
  await assert.rejects(
    receiveHostedCreativePlanCallback(
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
    /unavailable format/,
  );
  assert.equal(DB.plans.length, 0);
});

test("creative callback rejects copy-only workspace treatments before canonical writes", async () => {
  const fixture = creativeFixture();
  for (const item of fixture.plan.treatments) {
    item.artifact_requests = item.artifact_requests.filter(
      (request) => request.capability_id === "copy.text",
    );
  }
  fixture.result.output.media_plan_sha256 = digest(fixture.plan);
  const DB = new CreativeCallbackDb(fixture.campaign, fixture.task);
  await assert.rejects(
    receiveHostedCreativePlanCallback(
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
    /format capability/,
  );
  assert.equal(DB.plans.length, 0);
  assert.equal(fixture.task.callback_id, null);
});

test("exact media review is durable but still does not create an execution action", async () => {
  const DB = decisionDb();
  const result = await decideMediaPlan({ DB }, account, "campaign-1", {
    media_plan_id: "plan-1",
    media_plan_sha256: "7".repeat(64),
    reviewer_id: "reviewer-1",
    decision: "approved",
    projection_revision: 4,
  });
  assert.equal(result.decision, "approved");
  assert.equal(result.publication_allowed, true);
  const statements = DB.batches[0];
  assert.equal(statements.length, 5);
  const sql = statements.map((statement) => statement.sql).join("\n");
  assert.match(sql, /scope, target_kind/);
  assert.doesNotMatch(sql, /hosted_marketing_tool_actions/);
});

test("approved assisted campaign binds an existing candidate by exact experiment lineage", async () => {
  const DB = decisionDb();
  const result = await bindCandidateAssignment({ DB }, account, "campaign-1", {
    assignment_id: "assignment-1",
    candidate_id: "candidate-1",
    hypothesis_id: "challenger",
    treatment_id: "treatment-challenger",
    eligible_block_id: "block-1",
    candidate_revision: 2,
    projection_revision: 5,
  });
  assert.equal(result.duplicate, false);
  assert.equal(result.candidate_revision, 3);
  assert.equal(result.projection_revision, 6);
  const statements = DB.batches[0];
  assert.equal(statements.length, 4);
  assert.match(statements[0].sql, /hosted_marketing_post_assignments/);
  assert.match(statements[1].sql, /UPDATE hosted_workspace_candidates/);
  assert.match(statements[3].sql, /candidate_assigned/);
});

test("shadow or source-only campaign cannot bind a candidate", async () => {
  for (const DB of [
    decisionDb({ mode: "shadow" }),
    decisionDb({ publicationAllowed: 0 }),
  ]) {
    await assert.rejects(
      bindCandidateAssignment({ DB }, account, "campaign-1", {
        assignment_id: "assignment-1",
        candidate_id: "candidate-1",
        hypothesis_id: "challenger",
        treatment_id: "treatment-challenger",
        eligible_block_id: "block-1",
        candidate_revision: 2,
        projection_revision: 5,
      }),
      /assignment gate/,
    );
    assert.equal(DB.batches.length, 0);
  }
});

test("human approval and assignment routes require control-plane authority", async () => {
  for (const suffix of ["strategy-approval", "media-approval", "assignments"]) {
    const response = await handleHostedMarketingAgent(
      new Request(`https://workspace.example/api/marketing-agent/campaigns/campaign-1/${suffix}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}",
      }),
      { CONTROL_PLANE_TOKEN: "secret" },
      account,
    );
    assert.equal(response.status, 401);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }
});

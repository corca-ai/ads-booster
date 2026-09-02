import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { receiveHostedCandidateMaterializationCallback } from
  "../src/hosted-candidate-materialization-callback.js";
import { receiveHostedExperimentEvaluationCallback } from
  "../src/hosted-experiment-evaluation-callback.js";
import { receiveHostedLearningSynthesisCallback } from
  "../src/hosted-learning-synthesis-callback.js";
import { deriveExperimentEvaluation } from "../src/experiment-evaluation.js";
import {
  createShadowCampaign,
  createVariantLink,
  decideLearningCandidate,
  ingestProductEvent,
  requestCandidateMaterialization,
  requestExperimentEvaluation,
  requestLearningSynthesis,
} from "../src/marketing-agent.js";
import { D1Adapter } from "./d1-fixture.js";

const ACCOUNT = {
  account_id: "trace_kr",
  country: "KR",
  language: "ko",
  timezone: "Asia/Seoul",
};

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

function seedSupervisedCampaign(DB) {
  const now = new Date().toISOString();
  const packet = {
    schema_version: "trace.feature-evidence.v1",
    packet_id: "packet-installed-1",
    feature_id: "trace.lockscreen.ai-concepts",
    title: "AI 잠금화면 컨셉 정하기",
    lifecycle: "installed_confirmed",
    repository: "corca-ai/trace",
    mutable_ref: "develop",
    resolved_commit_sha: "a".repeat(40),
    tree_sha: "b".repeat(40),
    claims: [{
      claim_id: "claim-installed",
      text: "A chosen character appears in scheduled lock-screen scenes.",
      status: "installed_confirmed",
      evidence_ids: ["runtime-1"],
    }],
    evidence: [{
      evidence_id: "runtime-1",
      kind: "runtime_observation",
      source_uri: "trace-install://receipt-1",
      immutable_ref: "install-1",
      content_sha256: "c".repeat(64),
      result: "observed",
      collected_at: now,
    }],
    limitations: [],
    gate: {
      publication_allowed: true,
      allowed_claim_ids: ["claim-installed"],
      blocked_claim_ids: [],
      reasons: ["installed runtime observed"],
    },
    observed_at: now,
  };
  const packetSha = digest(packet);
  const registration = {
    experiment_id: "experiment-1",
    manipulated_component: "value frame",
    held_constant_components: ["account", "posting slot"],
    allowed_incidental_differences: [],
    activated_hypothesis_ids: ["control", "challenger"],
    primary_outcome: {
      name: "setup_completed",
      scope: "direct_response_attribution",
      window_hours: 72,
      causal_estimand: null,
    },
    diagnostic_metrics: ["views"],
    guardrails: ["product fidelity"],
    minimum_eligible_blocks: 2,
    maximum_posts: 4,
    maximum_duration_hours: 336,
    minimum_attribution_coverage_basis_points: 8000,
    stop_rules: ["guardrail failure"],
    inconclusive_when: ["insufficient blocks"],
  };
  const hypotheses = ["control", "challenger"].map((id) => ({
    hypothesis_id: id,
    role: id === "control" ? "control" : "challenger",
    claim_ids: ["claim-installed"],
    value_frame: id,
    rationale: `rationale ${id}`,
    falsifier: `falsifier ${id}`,
    proof_requirement: "Show the installed schedule.",
    conversation_motive: "Ask which moment viewers want.",
    reference_ids: [],
  }));
  const brief = {
    schema_version: "trace.strategy-brief.v1",
    brief_id: "brief-1",
    campaign_id: "campaign-1",
    account_id: ACCOUNT.account_id,
    feature_packet_id: packet.packet_id,
    feature_packet_sha256: packetSha,
    context_receipt_sha256: "1".repeat(64),
    business_outcome: "Increase completed lock-screen setups.",
    audience_situation: "An iPhone user wants a character to accompany the day.",
    belief_to_change: "A lock screen can evolve through the day.",
    hypotheses,
    experiment: registration,
    created_at: now,
  };
  const treatments = hypotheses.map((hypothesis) => {
    const request = {
      request_id: `request-${hypothesis.hypothesis_id}`,
      capability_id: "copy.text",
      proof_kind: "copy_only",
      claim_ids: ["claim-installed"],
      instructions: "Materialize approved copy.",
    };
    return {
      treatment_id: `treatment-${hypothesis.hypothesis_id}`,
      hypothesis_id: hypothesis.hypothesis_id,
      format: "text_only",
      hook: `hook ${hypothesis.hypothesis_id}`,
      caption_direction: "Show a day sequence.",
      manipulated_component_value: hypothesis.hypothesis_id,
      proof_narrative: "Use only the installed claim.",
      claim_ids: ["claim-installed"],
      artifact_requests: [request],
    };
  });
  const plan = {
    schema_version: "trace.media-plan.v1",
    plan_id: "plan-1",
    campaign_id: "campaign-1",
    account_id: ACCOUNT.account_id,
    experiment_id: registration.experiment_id,
    strategy_brief_sha256: digest(brief),
    context_receipt_sha256: "1".repeat(64),
    treatments,
    publication_allowed: true,
    human_review_required: true,
    created_at: now,
  };
  const knowledgeSnapshot = {
    principles: [
      "한 게시물은 한 사람의 한 상황과 한 가지 믿음 변화에 집중한다.",
      "제품 주장을 먼저 잠그고 그 주장을 증명할 proof를 매체보다 먼저 선택한다.",
    ],
  };
  const knowledgeSnapshotSha = digest(knowledgeSnapshot);
  DB.sqlite.exec(`
    INSERT INTO hosted_workspace_accounts
      (account_id, display_name, country, language, timezone, morning_time, evening_time,
       revision, created_at, updated_at)
    VALUES ('trace_kr', 'Trace KR', 'KR', 'ko', 'Asia/Seoul', '07:30', '19:30', 1, 1, 1);
    INSERT INTO mac_workers
      (worker_id, display_name, pool, state, capabilities_json, doctor_json,
       created_at, updated_at)
    VALUES ('worker-1', 'Mac', 'appium', 'active',
            '{"task_kinds":"marketing_judgment"}', '{}', 'now', 'now');
  `);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_feature_packets
      (packet_id, feature_id, schema_version, lifecycle, repository, mutable_ref,
       resolved_commit_sha, tree_sha, packet_json, packet_sha256, publication_allowed,
       observed_at, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)`,
  ).run(
    packet.packet_id,
    packet.feature_id,
    packet.schema_version,
    packet.lifecycle,
    packet.repository,
    packet.mutable_ref,
    packet.resolved_commit_sha,
    packet.tree_sha,
    canonicalJson(packet),
    packetSha,
    now,
    now,
  );
  DB.sqlite.exec(`
    INSERT INTO hosted_marketing_campaigns
      (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
       mode, state, projection_revision, business_outcome, created_at, updated_at)
    VALUES ('origin-1', 'trace_kr', 'packet-installed-1', '${packetSha}', 'agent_v1',
            'shadow', 'completed', 1, 'origin', '${now}', '${now}');
    INSERT INTO hosted_marketing_campaigns
      (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
       mode, origin_campaign_id, state, projection_revision, business_outcome,
       created_at, updated_at)
    VALUES ('campaign-1', 'trace_kr', 'packet-installed-1', '${packetSha}', 'agent_v1',
            'assisted', 'origin-1', 'creative_planned', 4, 'outcome', '${now}', '${now}');
  `);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_knowledge_snapshots
      (campaign_id, schema_version, snapshot_json, snapshot_sha256, created_at)
     VALUES ('campaign-1', 'trace.marketing-knowledge.v1', ?, ?, ?)`,
  ).run(canonicalJson(knowledgeSnapshot), knowledgeSnapshotSha, now);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_product_truth_approvals
      (approval_id, packet_id, packet_sha256, approved_claim_ids_json,
       decision, reviewer_id, reviewed_at)
     VALUES ('truth-1', ?, ?, ?, 'approved', 'reviewer-1', ?)`,
  ).run(packet.packet_id, packetSha, '["claim-installed"]', now);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_context_receipts
      (receipt_id, campaign_id, schema_version, receipt_json, receipt_sha256,
       feature_packet_sha256, knowledge_snapshot_sha256, capability_snapshot_sha256,
       prompt_sha256, output_schema_sha256, created_at)
     VALUES ('receipt-1', 'campaign-1', 'trace.context-receipt.v1', '{}', ?, ?, ?, ?, ?, ?, ?)`,
  ).run("1".repeat(64), packetSha, "2".repeat(64), "3".repeat(64), "4".repeat(64), "5".repeat(64), now);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_strategy_briefs
      (brief_id, campaign_id, context_receipt_id, schema_version, brief_json,
       brief_sha256, created_at)
     VALUES ('brief-1', 'campaign-1', 'receipt-1', 'trace.strategy-brief.v1', ?, ?, ?)`,
  ).run(canonicalJson(brief), digest(brief), now);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_experiments
      (experiment_id, campaign_id, strategy_brief_id, state, primary_outcome_scope,
       registration_json, registration_sha256, created_at, updated_at)
     VALUES ('experiment-1', 'campaign-1', 'brief-1', 'registered',
             'direct_response_attribution', ?, ?, ?, ?)`,
  ).run(canonicalJson(registration), digest(registration), now, now);
  for (const hypothesis of hypotheses) {
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_hypotheses
        (hypothesis_id, campaign_id, strategy_brief_id, portfolio_role,
         hypothesis_json, hypothesis_sha256, created_at)
       VALUES (?, 'campaign-1', 'brief-1', ?, ?, ?, ?)`,
    ).run(
      hypothesis.hypothesis_id,
      hypothesis.role,
      canonicalJson(hypothesis),
      digest(hypothesis),
      now,
    );
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_experiment_arms
        (arm_id, experiment_id, hypothesis_id, treatment_json, treatment_sha256,
         allocation_weight, created_at)
       VALUES (?, 'experiment-1', ?, ?, ?, 1, ?)`,
    ).run(
      `experiment-1.${hypothesis.hypothesis_id}`,
      hypothesis.hypothesis_id,
      canonicalJson(hypothesis),
      digest(hypothesis),
      now,
    );
  }
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_media_plans
      (plan_id, campaign_id, strategy_brief_id, context_receipt_id, schema_version,
       plan_json, plan_sha256, publication_allowed, human_review_required, state,
       created_at, updated_at)
     VALUES ('plan-1', 'campaign-1', 'brief-1', 'receipt-1', 'trace.media-plan.v1',
             ?, ?, 1, 1, 'approved', ?, ?)`,
  ).run(canonicalJson(plan), digest(plan), now, now);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_approval_grants
      (grant_id, campaign_id, scope, target_kind, target_id, target_sha256,
       decision, reviewer_id, reviewed_at)
     VALUES ('creative-grant-1', 'campaign-1', 'creative', 'media_plan',
             'plan-1', ?, 'approved', 'reviewer-1', ?)`,
  ).run(digest(plan), now);
  for (const treatment of treatments) {
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_creative_treatments
        (treatment_id, plan_id, campaign_id, experiment_id, hypothesis_id,
         format, treatment_json, treatment_sha256, created_at)
       VALUES (?, 'plan-1', 'campaign-1', 'experiment-1', ?, 'text_only', ?, ?, ?)`,
    ).run(
      treatment.treatment_id,
      treatment.hypothesis_id,
      canonicalJson(treatment),
      digest(treatment),
      now,
    );
    const request = treatment.artifact_requests[0];
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_artifact_requests
        (request_id, campaign_id, treatment_id, capability_id, proof_kind,
         request_json, request_sha256, state, created_at, updated_at)
       VALUES (?, 'campaign-1', ?, 'copy.text', 'copy_only', ?, ?, 'approved', ?, ?)`,
    ).run(
      request.request_id,
      treatment.treatment_id,
      canonicalJson(request),
      digest(request),
      now,
      now,
    );
  }
  return { registration };
}

function claimTask(DB, taskId) {
  DB.sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = ?, execution_started_at = 'now'
     WHERE task_id = ?`,
  ).run(`lease-${taskId}`, taskId);
  return DB.sqlite.prepare(
    "SELECT * FROM hosted_workspace_capture_tasks WHERE task_id = ?",
  ).get(taskId);
}

async function materializeOne(DB, projectionRevision) {
  const requested = await requestCandidateMaterialization(
    { DB },
    ACCOUNT,
    "campaign-1",
    { projection_revision: projectionRevision },
  );
  const task = claimTask(DB, requested.task_id);
  const payload = JSON.parse(task.task_json).payload;
  const knowledge = DB.sqlite.prepare(
    `SELECT snapshot_json, snapshot_sha256 FROM hosted_marketing_knowledge_snapshots
     WHERE campaign_id = 'campaign-1'`,
  ).get();
  assert.deepEqual(payload.canonical_principles, JSON.parse(knowledge.snapshot_json).principles);
  assert.equal(payload.knowledge_snapshot_sha256, knowledge.snapshot_sha256);
  const candidate = {
    schema_version: "trace.candidate-materialization.v1",
    topic: `topic ${requested.assignment_id}`,
    country: "KR",
    caption: `caption ${requested.hypothesis_id}`,
    hypothesis: `hypothesis ${requested.hypothesis_id}`,
    posting_slot: "morning",
    appium_prompt: "Capture the installed schedule.",
    image_inputs: {
      trace_items: [
        "07:00 Wake up",
        "09:00 Work",
        "12:00 Lunch",
        "18:00 Commute",
        "22:00 Sleep",
      ],
      device_time: "09:41",
      background_subject: "character_other",
      background_mood: "warm",
      background_search_query: null,
      language: "ko",
    },
    claim_ids: ["claim-installed"],
  };
  const receipt = {
    schema_version: "trace.context-receipt.v1",
    receipt_id: task.task_id,
    campaign_id: "campaign-1",
    feature_packet_id: payload.feature_packet.packet_id,
    feature_packet_sha256: payload.feature_packet_sha256,
    knowledge_snapshot_sha256: payload.knowledge_snapshot_sha256,
    capability_snapshot_sha256: digest({ capabilities: [] }),
    prompt_version: "trace.evidence-bound-candidate.v1",
    prompt_sha256: "6".repeat(64),
    output_schema_version: "trace.candidate-materialization.v1",
    output_schema_sha256: "7".repeat(64),
    included_record_ids: [payload.strategy_brief.brief_id, payload.media_plan.plan_id],
    omitted_modules: ["external_references", "unapproved_learning"],
    created_at: task.created_at,
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
        judgment: "candidate_materialization",
        campaign_id: "campaign-1",
        assignment_id: requested.assignment_id,
        eligible_block_id: requested.eligible_block_id,
        treatment_id: requested.treatment_id,
        context_receipt: receipt,
        context_receipt_sha256: digest(receipt),
        candidate,
        candidate_sha256: digest(candidate),
        tool_actions_created: 0,
      },
    },
  };
  const accepted = await receiveHostedCandidateMaterializationCallback(
    { DB },
    task,
    callback,
    { worker_id: "worker-1" },
  );
  return { ...requested, ...accepted };
}

test("assisted loop materializes balanced blocks and evaluates attributed outcomes", async () => {
  const DB = new D1Adapter();
  const { registration } = seedSupervisedCampaign(DB);
  const materialized = [];
  for (let revision = 4; revision < 8; revision += 1) {
    try {
      materialized.push(await materializeOne(DB, revision));
    } catch (error) {
      const states = DB.sqlite.prepare(
        "SELECT request_id, state FROM hosted_marketing_artifact_requests ORDER BY request_id",
      ).all();
      assert.fail(`${error.message}: ${JSON.stringify(states)}`);
    }
  }
  assert.deepEqual(
    [...new Set(materialized.map((item) => item.eligible_block_id))].sort(),
    ["experiment-1.block-1", "experiment-1.block-2"],
  );
  assert.equal(
    DB.sqlite.prepare("SELECT COUNT(*) AS count FROM hosted_marketing_post_assignments").get().count,
    4,
  );
  assert.equal(
    DB.sqlite.prepare("SELECT COUNT(*) AS count FROM hosted_marketing_artifact_manifests").get().count,
    4,
  );

  DB.sqlite.prepare(
    `INSERT INTO hosted_threads_profiles
      (profile_id, account_id, threads_user_id, username, scopes_json,
       token_ciphertext, token_nonce, token_key_version, state, created_at, updated_at)
     VALUES ('profile-1', 'trace_kr', 'threads-1', 'trace', '["threads_basic"]',
             X'01', X'02', 'v1', 'active', 'now', 'now')`,
  ).run();
  const assignments = DB.sqlite.prepare(
    `SELECT assignment.assignment_id, assignment.hypothesis_id, assignment.candidate_id
     FROM hosted_marketing_post_assignments AS assignment ORDER BY assignment.assignment_id`,
  ).all();
  const publishedAt = new Date(Date.now() - 100 * 60 * 60 * 1000).toISOString();
  const expiresAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();
  for (const [index, assignment] of assignments.entries()) {
    const link = await createVariantLink(
      { DB },
      ACCOUNT,
      "campaign-1",
      assignment.assignment_id,
      { destination_uri: "https://trace.example/setup", expires_at: expiresAt },
    );
    DB.sqlite.prepare(
      `INSERT INTO hosted_threads_publications
        (publication_id, account_id, candidate_id, candidate_revision, profile_id,
         state, caption_snapshot, image_key_snapshot, image_sha256_snapshot,
         timezone_snapshot, posting_slot_snapshot, scheduled_at, threads_post_id,
         permalink, published_at, created_at, updated_at, marketing_assignment_id)
       VALUES (?, 'trace_kr', ?, 2, 'profile-1', 'published', 'caption', 'image-key', ?,
               'Asia/Seoul', 'morning', ?, ?, ?, ?, ?, ?, ?)`,
    ).run(
      `publication-${index}`,
      assignment.candidate_id,
      "8".repeat(64),
      publishedAt,
      `post-${index}`,
      `https://threads.net/post-${index}`,
      publishedAt,
      publishedAt,
      publishedAt,
      assignment.assignment_id,
    );
    if (assignment.hypothesis_id === "challenger") {
      await ingestProductEvent(
        { DB, TRACE_EVENT_HASH_SALT: "test-salt" },
        {
          event_id: `event-${index}`,
          event_version: "trace.product-event.v1",
          event_type: "setup_completed",
          variant_token: link.token,
          install_id: `install-${index}`,
          occurred_at: new Date(Date.parse(publishedAt) + 60 * 60 * 1000).toISOString(),
          payload: {},
        },
      );
    }
  }
  const requested = await requestExperimentEvaluation(
    { DB },
    ACCOUNT,
    "campaign-1",
    { projection_revision: 8 },
  );
  const task = claimTask(DB, requested.task_id);
  const request = JSON.parse(task.task_json).payload.request;
  assert.equal(request.observations.length, 4);
  assert.equal(request.observations.filter((item) => item.converted).length, 2);
  const evaluation = deriveExperimentEvaluation(request);
  assert.deepEqual(evaluation, {
    schema_version: "trace.experiment-evaluation.v1",
    evaluation_id: requested.evaluation_id,
    campaign_id: "campaign-1",
    experiment_id: registration.experiment_id,
    state: "evaluated",
    outcome_scope: "direct_response_attribution",
    winner_hypothesis_id: "challenger",
    eligible_blocks: 2,
    attribution_coverage_basis_points: 10000,
    guardrail_failures: [],
    lineage_ids: request.observations.map((observation) => observation.assignment_id),
    interpretation: "challenger has the highest observed direct-response attribution rate inside complete eligible blocks. This is descriptive attribution, not a causal effect.",
    evaluated_at: request.evaluated_at,
  });
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
        judgment: "experiment_evaluation",
        evaluation,
        evaluation_sha256: digest(evaluation),
        tool_actions_created: 0,
      },
    },
  };
  const evaluationMutationSnapshot = () => ({
    attribution_observations: DB.sqlite.prepare(
      "SELECT COUNT(*) AS count FROM hosted_marketing_attribution_observations",
    ).get().count,
    evaluations: DB.sqlite.prepare(
      "SELECT COUNT(*) AS count FROM hosted_marketing_experiment_evaluations",
    ).get().count,
    experiment: DB.sqlite.prepare(
      `SELECT state, updated_at FROM hosted_marketing_experiments WHERE experiment_id = 'experiment-1'`,
    ).get(),
    campaign: DB.sqlite.prepare(
      `SELECT state, projection_revision, updated_at
       FROM hosted_marketing_campaigns WHERE campaign_id = 'campaign-1'`,
    ).get(),
    task: DB.sqlite.prepare(
      `SELECT callback_id, callback_reservation_id, result_json
       FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
    ).get(task.task_id),
    evaluation_events: DB.sqlite.prepare(
      `SELECT COUNT(*) AS count FROM hosted_marketing_run_events
       WHERE campaign_id = 'campaign-1' AND event_type = 'experiment_evaluated'`,
    ).get().count,
  });
  const beforeRejectedEvaluation = evaluationMutationSnapshot();
  const assertRejectedEvaluation = async (tamperedEvaluation) => {
    const tampered = structuredClone(callback);
    tampered.result.output.evaluation = tamperedEvaluation;
    tampered.result.output.evaluation_sha256 = digest(tamperedEvaluation);
    await assert.rejects(
      receiveHostedExperimentEvaluationCallback(
        { DB },
        task,
        tampered,
        { worker_id: "worker-1" },
      ),
      (error) => error.status === 409,
    );
    assert.deepEqual(evaluationMutationSnapshot(), beforeRejectedEvaluation);
  };
  await assertRejectedEvaluation({
    ...evaluation,
    winner_hypothesis_id: "control",
    interpretation: "control has the highest observed direct-response attribution rate inside complete eligible blocks. This is descriptive attribution, not a causal effect.",
  });
  await assertRejectedEvaluation({
    ...evaluation,
    attribution_coverage_basis_points: 9_999,
  });
  await assertRejectedEvaluation({
    ...evaluation,
    state: "inconclusive",
    winner_hypothesis_id: null,
    interpretation: "Direct-response attribution is tied across active hypotheses.",
  });
  const accepted = await receiveHostedExperimentEvaluationCallback(
    { DB },
    task,
    callback,
    { worker_id: "worker-1" },
  );
  assert.equal(accepted.state, "evaluated");
  assert.equal(
    DB.sqlite.prepare("SELECT COUNT(*) AS count FROM hosted_marketing_attribution_observations")
      .get().count,
    4,
  );
  assert.equal(
    DB.sqlite.prepare(
      "SELECT state FROM hosted_marketing_campaigns WHERE campaign_id = 'campaign-1'",
    ).get().state,
    "evaluated",
  );

  const secondEvaluatedAt = new Date().toISOString();
  const secondHypothesis = {
    hypothesis_id: "challenger-2",
    role: "challenger",
    claim_ids: ["claim-installed"],
    value_frame: "character day narrative",
    rationale: "Replicate the narrative direction.",
    falsifier: "No attributed setup lift.",
    proof_requirement: "Show installed schedule scenes.",
    conversation_motive: "Ask for a favorite time slot.",
    reference_ids: [],
  };
  const secondTreatment = {
    treatment_id: "treatment-challenger-2",
    hypothesis_id: secondHypothesis.hypothesis_id,
    format: "text_only",
    hook: "A character lives through your day.",
    caption_direction: "Explain the scheduled sequence.",
    manipulated_component_value: "character day narrative",
    proof_narrative: "Use installed evidence only.",
    claim_ids: ["claim-installed"],
    artifact_requests: [{
      request_id: "request-challenger-2",
      capability_id: "copy.text",
      proof_kind: "copy_only",
      claim_ids: ["claim-installed"],
      instructions: "Materialize copy.",
    }],
  };
  const secondEvaluation = {
    schema_version: "trace.experiment-evaluation.v1",
    evaluation_id: "evaluation-2",
    campaign_id: "campaign-2",
    experiment_id: "experiment-2",
    state: "evaluated",
    winner_hypothesis_id: secondHypothesis.hypothesis_id,
    eligible_blocks: 2,
    attribution_coverage_basis_points: 10000,
    guardrail_failures: [],
    interpretation: "Replicated direct-response direction; not a causal effect.",
    evaluated_at: secondEvaluatedAt,
  };
  const packetSha = DB.sqlite.prepare(
    "SELECT packet_sha256 FROM hosted_marketing_feature_packets WHERE packet_id = 'packet-installed-1'",
  ).get().packet_sha256;
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_campaigns
      (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
       mode, origin_campaign_id, state, projection_revision, business_outcome,
       created_at, updated_at)
     VALUES ('campaign-2', 'trace_kr', 'packet-installed-1', ?, 'agent_v1', 'assisted',
             'origin-1', 'evaluated', 2, 'replication', ?, ?)`,
  ).run(packetSha, secondEvaluatedAt, secondEvaluatedAt);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_context_receipts
      (receipt_id, campaign_id, schema_version, receipt_json, receipt_sha256,
       feature_packet_sha256, knowledge_snapshot_sha256, capability_snapshot_sha256,
       prompt_sha256, output_schema_sha256, created_at)
     VALUES ('receipt-2', 'campaign-2', 'trace.context-receipt.v1', '{}', ?, ?, ?, ?, ?, ?, ?)`,
  ).run(
    "a".repeat(64),
    packetSha,
    "b".repeat(64),
    "c".repeat(64),
    "d".repeat(64),
    "e".repeat(64),
    secondEvaluatedAt,
  );
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_strategy_briefs
      (brief_id, campaign_id, context_receipt_id, schema_version, brief_json,
       brief_sha256, created_at)
     VALUES ('brief-2', 'campaign-2', 'receipt-2', 'trace.strategy-brief.v1', '{}', ?, ?)`,
  ).run("f".repeat(64), secondEvaluatedAt);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_experiments
      (experiment_id, campaign_id, strategy_brief_id, state, primary_outcome_scope,
       registration_json, registration_sha256, created_at, updated_at)
     VALUES ('experiment-2', 'campaign-2', 'brief-2', 'evaluated',
             'direct_response_attribution', '{}', ?, ?, ?)`,
  ).run("0".repeat(64), secondEvaluatedAt, secondEvaluatedAt);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_hypotheses
      (hypothesis_id, campaign_id, strategy_brief_id, portfolio_role,
       hypothesis_json, hypothesis_sha256, created_at)
     VALUES (?, 'campaign-2', 'brief-2', 'challenger', ?, ?, ?)`,
  ).run(
    secondHypothesis.hypothesis_id,
    canonicalJson(secondHypothesis),
    digest(secondHypothesis),
    secondEvaluatedAt,
  );
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_media_plans
      (plan_id, campaign_id, strategy_brief_id, context_receipt_id, schema_version,
       plan_json, plan_sha256, publication_allowed, human_review_required, state,
       created_at, updated_at)
     VALUES ('plan-2', 'campaign-2', 'brief-2', 'receipt-2', 'trace.media-plan.v1',
             '{}', ?, 1, 1, 'approved', ?, ?)`,
  ).run("9".repeat(64), secondEvaluatedAt, secondEvaluatedAt);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_creative_treatments
      (treatment_id, plan_id, campaign_id, experiment_id, hypothesis_id, format,
       treatment_json, treatment_sha256, created_at)
     VALUES (?, 'plan-2', 'campaign-2', 'experiment-2', ?, 'text_only', ?, ?, ?)`,
  ).run(
    secondTreatment.treatment_id,
    secondHypothesis.hypothesis_id,
    canonicalJson(secondTreatment),
    digest(secondTreatment),
    secondEvaluatedAt,
  );
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_experiment_evaluations
      (evaluation_id, campaign_id, experiment_id, schema_version, state,
       evaluation_json, evaluation_sha256, evaluated_at)
     VALUES (?, 'campaign-2', 'experiment-2', 'trace.experiment-evaluation.v1',
             'evaluated', ?, ?, ?)`,
  ).run(
    secondEvaluation.evaluation_id,
    canonicalJson(secondEvaluation),
    digest(secondEvaluation),
    secondEvaluatedAt,
  );
  const learningRequest = await requestLearningSynthesis(
    { DB },
    ACCOUNT,
    {
      learning_id: "learning-1",
      evaluation_ids: [requested.evaluation_id, secondEvaluation.evaluation_id],
    },
  );
  const learningTask = claimTask(DB, learningRequest.task_id);
  const learningPayload = JSON.parse(learningTask.task_json).payload;
  const learningCandidate = {
    schema_version: "trace.learning-candidate.v1",
    learning_id: "learning-1",
    campaign_id: "campaign-2",
    statement: "Character-day framing may improve attributed setup completion.",
    scope: "KR iPhone installed-evidence campaigns",
    independent_lineage_ids: learningPayload.lineages.map(
      (item) => item.evaluation.evaluation_id,
    ),
    status: "candidate",
    created_at: secondEvaluatedAt,
  };
  const learningCallback = {
    callback_id: `${learningTask.task_id}:completed`,
    task_id: learningTask.task_id,
    run_id: learningTask.run_id,
    account_id: learningTask.account_id,
    kind: "marketing_judgment",
    result: {
      status: "succeeded",
      output: {
        pipeline: "hosted_marketing_judgment_v1",
        judgment: "learning_synthesis",
        learning_candidate: learningCandidate,
        learning_candidate_sha256: digest(learningCandidate),
        limitations: ["Direct-response attribution is not a causal effect."],
        tool_actions_created: 0,
      },
    },
  };
  const learningAccepted = await receiveHostedLearningSynthesisCallback(
    { DB },
    learningTask,
    learningCallback,
    { worker_id: "worker-1" },
  );
  assert.equal(learningAccepted.state, "candidate");
  const learningSha = digest(learningCandidate);
  const approval = await decideLearningCandidate({ DB }, ACCOUNT, "learning-1", {
    candidate_sha256: learningSha,
    reviewer_id: "reviewer-2",
    decision: "approved",
  });
  assert.equal(approval.decision, "approved");
  assert.ok(approval.principle_id);

  const shadowPacket = {
    ...JSON.parse(DB.sqlite.prepare(
      "SELECT packet_json FROM hosted_marketing_feature_packets WHERE packet_id = 'packet-installed-1'",
    ).get().packet_json),
    packet_id: "packet-shadow-next",
    lifecycle: "source_candidate",
    claims: [{
      claim_id: "claim-next",
      text: "A character can be configured for scheduled scenes.",
      status: "source_supported",
      evidence_ids: ["runtime-1"],
    }],
    gate: {
      publication_allowed: false,
      allowed_claim_ids: [],
      blocked_claim_ids: ["claim-next"],
      reasons: ["shadow strategy only"],
    },
  };
  const nextCampaign = await createShadowCampaign({ DB }, ACCOUNT, {
    campaign_id: "campaign-next",
    business_outcome: "Test the next bounded format.",
    current_control: "아이폰 쓰는 유저들...",
    feature_packet: shadowPacket,
    research_enabled: false,
  });
  const nextTask = DB.sqlite.prepare(
    "SELECT task_json FROM hosted_workspace_capture_tasks WHERE task_id = ?",
  ).get(nextCampaign.task_id);
  const nextKnowledge = DB.sqlite.prepare(
    `SELECT snapshot_json, snapshot_sha256 FROM hosted_marketing_knowledge_snapshots
     WHERE campaign_id = 'campaign-next'`,
  ).get();
  assert.equal(nextKnowledge.snapshot_sha256, JSON.parse(nextTask.task_json).payload.knowledge_snapshot_sha256);
  assert.deepEqual(
    JSON.parse(nextKnowledge.snapshot_json).principles,
    JSON.parse(nextTask.task_json).payload.canonical_principles,
  );
  assert.ok(
    JSON.parse(nextTask.task_json).payload.canonical_principles.includes(
      learningCandidate.statement,
    ),
  );
});

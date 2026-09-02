import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { receiveHostedCandidateMaterializationCallback } from
  "../src/hosted-candidate-materialization-callback.js";
import { receiveHostedExperimentEvaluationCallback } from
  "../src/hosted-experiment-evaluation-callback.js";
import { receiveHostedLearningSynthesisCallback } from
  "../src/hosted-learning-synthesis-callback.js";
import { receiveHostedOutcomeReassessmentCallback } from
  "../src/hosted-outcome-reassessment-callback.js";
import { deriveExperimentEvaluation } from "../src/experiment-evaluation.js";
import { handleHostedWorkspace } from "../src/hosted-workspace.js";
import {
  createShadowCampaign,
  createVariantLink,
  decideLearningCandidate,
  ingestProductEvent,
  handleHostedMarketingAgent,
  normalizeFeaturePacket,
  requestCandidateMaterialization,
  requestExperimentEvaluation,
  requestLearningSynthesis,
} from "../src/marketing-agent.js";
import {
  listMarketingReviewQueue,
  marketingReviewPacket,
} from "../src/marketing-review.js";
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

function weeklyImageInputs() {
  const colors = ["2D936C", "00B4D8", "F9C74F", "F26419", "DA4C93"];
  return {
    trace_items: Array.from({ length: 18 }, (_, index) => ({
      title: `일정 ${index + 1}`,
      day: index % 7,
      days: index < 4 ? 2 : 1,
      time: index < 4 ? `${String(7 + index).padStart(2, "0")}:00` : null,
      color: colors[index % colors.length],
    })),
    trace_todos: Array.from({ length: 8 }, (_, index) => `할 일 ${index + 1}`),
    device_time: "09:41",
    background_subject: "character_other",
    background_mood: "warm",
    background_search_query: null,
    language: "ko",
  };
}

function seedSupervisedCampaign(DB, { reviewContext = false, causal = false } = {}) {
  const now = new Date().toISOString();
  const packet = normalizeFeaturePacket({
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
  });
  const packetSha = digest(packet);
  const registration = {
    experiment_id: "experiment-1",
    manipulated_component: "value frame",
    held_constant_components: ["account", "posting slot"],
    allowed_incidental_differences: [],
    activated_hypothesis_ids: ["control", "challenger"],
    primary_outcome: {
      name: "setup_completed",
      scope: causal ? "estimated_treatment_effect" : "direct_response_attribution",
      window_hours: 72,
      causal_estimand: causal ? "difference in setup completion probability" : null,
    },
    allocation_method: causal
      ? "server_randomized_complete_blocks_v1"
      : "balanced_complete_blocks",
    causal_treatment_hypothesis_id: causal ? "challenger" : null,
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
    decision_dossier: {
      schema_version: "trace.marketing-decision-dossier.v1",
      situation: "new_launch",
      selected_icp_id: "research_needed",
      selection_basis_ids: [],
      positioning: {
        category: "dynamic lock-screen companion",
        current_alternative: "a static lock-screen image",
        differentiated_mechanism: "one character changes through scheduled scenes",
        proof_claim_ids: ["claim-installed"],
      },
      evidence_dispositions: [{
        evidence_id: "runtime-1",
        disposition: "supports",
        confidence_basis_points: 10000,
        freshness: "unknown",
        use: "test",
        reason: "The runtime observation verifies the mechanism, not an ICP.",
      }],
      recommended_next_step: "research",
      reason: "A customer-backed audience is still required.",
      required_proof_ids: ["claim-installed"],
    },
    hypotheses,
    experiment: registration,
    created_at: now,
  };
  const treatments = hypotheses.map((hypothesis) => {
    const copyRequest = {
      request_id: `copy-${hypothesis.hypothesis_id}`,
      capability_id: "copy.text",
      proof_kind: "copy_only",
      claim_ids: ["claim-installed"],
      instructions: "Materialize approved copy.",
    };
    const captureRequest = {
      request_id: `capture-${hypothesis.hypothesis_id}`,
      capability_id: "capture.native_png",
      proof_kind: "installed_native_capture",
      claim_ids: ["claim-installed"],
      instructions: "Capture the approved Trace lock-screen treatment.",
    };
    return {
      treatment_id: `treatment-${hypothesis.hypothesis_id}`,
      hypothesis_id: hypothesis.hypothesis_id,
      format: "native_sequence",
      hook: `hook ${hypothesis.hypothesis_id}`,
      caption_direction: "Show a day sequence.",
      manipulated_component_value: hypothesis.hypothesis_id,
      proof_narrative: "Use only the installed claim.",
      claim_ids: ["claim-installed"],
      artifact_requests: [copyRequest, captureRequest],
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
            '{"task_kinds":"capture,marketing_judgment","feedback_context_v1":true,"candidate_materialization_v2":true}',
            '{}', 'now', 'now');
  `);
  if (causal) {
    DB.sqlite.exec(`
      INSERT INTO hosted_threads_profiles
        (profile_id, account_id, threads_user_id, username, scopes_json,
         token_ciphertext, token_nonce, token_key_version, state, created_at, updated_at)
      VALUES ('profile-1', 'trace_kr', 'threads-1', 'trace', '["threads_basic"]',
              X'01', X'02', 'v1', 'active', 'now', 'now');
      UPDATE hosted_workspace_accounts
      SET default_threads_profile_id = 'profile-1';
    `);
  }
  const copyCatalog = DB.sqlite.prepare(
    `SELECT descriptor_sha256, effect_class, request_schema_sha256, receipt_schema_sha256, owner_id
     FROM hosted_marketing_adapter_capabilities
     WHERE account_id = 'trace_kr' AND capability_id = 'copy.text'`,
  ).get();
  const captureCatalog = DB.sqlite.prepare(
    `SELECT descriptor_sha256, effect_class, request_schema_sha256, receipt_schema_sha256, owner_id
     FROM hosted_marketing_adapter_capabilities
     WHERE account_id = 'trace_kr' AND capability_id = 'capture.native_png'`,
  ).get();
  const copyBinding = {
    capability_id: "copy.text",
    descriptor_sha256: copyCatalog.descriptor_sha256,
    effect_class: copyCatalog.effect_class,
    request_schema_sha256: copyCatalog.request_schema_sha256,
    receipt_schema_sha256: copyCatalog.receipt_schema_sha256,
    owner_id: copyCatalog.owner_id,
  };
  const copyBindingSha256 = digest(copyBinding);
  const captureBinding = {
    capability_id: "capture.native_png",
    descriptor_sha256: captureCatalog.descriptor_sha256,
    effect_class: captureCatalog.effect_class,
    request_schema_sha256: captureCatalog.request_schema_sha256,
    receipt_schema_sha256: captureCatalog.receipt_schema_sha256,
    owner_id: captureCatalog.owner_id,
  };
  const captureBindingSha256 = digest(captureBinding);
  if (reviewContext) {
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_context_snapshots
        (snapshot_id, account_id, schema_version, snapshot_json, snapshot_sha256,
         approved_by, approved_at, expires_at, created_at)
       VALUES ('review-context-1', 'trace_kr', 'trace.marketing-context.v1', ?, ?,
               'reviewer-1', '2026-09-01T00:00:00Z', '2027-09-01T00:00:00Z',
               '2026-09-01T00:00:00Z')`,
    ).run(
      JSON.stringify({ raw_transcript: "private customer source must never enter this packet" }),
      "f".repeat(64),
    );
  }
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
       mode, origin_campaign_id, marketing_context_snapshot_id,
       marketing_context_snapshot_sha256, state, projection_revision, business_outcome,
       created_at, updated_at)
    VALUES ('campaign-1', 'trace_kr', 'packet-installed-1', '${packetSha}', 'agent_v1',
            'assisted', 'origin-1', ${reviewContext ? "'review-context-1'" : "NULL"},
            ${reviewContext ? `'${"f".repeat(64)}'` : "NULL"}, 'creative_planned', 4,
            'outcome', '${now}', '${now}');
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
  ).run(
    "1".repeat(64),
    packetSha,
    "2".repeat(64),
    digest({
      capability_bindings: [
        { ...captureBinding, binding_sha256: captureBindingSha256 },
        { ...copyBinding, binding_sha256: copyBindingSha256 },
      ],
    }),
    "4".repeat(64),
    "5".repeat(64),
    now,
  );
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_capability_bindings
      (context_receipt_id, capability_id, binding_sha256, descriptor_sha256, effect_class,
       request_schema_sha256, receipt_schema_sha256, owner_id, created_at)
     VALUES ('receipt-1', 'copy.text', ?, ?, ?, ?, ?, ?, ?)`,
  ).run(
    copyBindingSha256,
    copyBinding.descriptor_sha256,
    copyBinding.effect_class,
    copyBinding.request_schema_sha256,
    copyBinding.receipt_schema_sha256,
    copyBinding.owner_id,
    now,
  );
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_capability_bindings
      (context_receipt_id, capability_id, binding_sha256, descriptor_sha256, effect_class,
       request_schema_sha256, receipt_schema_sha256, owner_id, created_at)
     VALUES ('receipt-1', 'capture.native_png', ?, ?, ?, ?, ?, ?, ?)`,
  ).run(
    captureBindingSha256,
    captureBinding.descriptor_sha256,
    captureBinding.effect_class,
    captureBinding.request_schema_sha256,
    captureBinding.receipt_schema_sha256,
    captureBinding.owner_id,
    now,
  );
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_strategy_briefs
      (brief_id, campaign_id, context_receipt_id, schema_version, brief_json,
       brief_sha256, created_at)
     VALUES ('brief-1', 'campaign-1', 'receipt-1', 'trace.strategy-brief.v1', ?, ?, ?)`,
  ).run(canonicalJson(brief), digest(brief), now);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_experiments
      (experiment_id, campaign_id, strategy_brief_id, state, primary_outcome_scope,
       allocation_method, randomization_seed, randomization_seed_sha256,
       registration_json, registration_sha256, created_at, updated_at)
     VALUES ('experiment-1', 'campaign-1', 'brief-1', 'registered', ?, ?, ?, ?, ?, ?, ?, ?)`,
  ).run(
    registration.primary_outcome.scope,
    registration.allocation_method,
    causal ? "a".repeat(64) : null,
    causal ? createHash("sha256").update("a".repeat(64)).digest("hex") : null,
    canonicalJson(registration),
    digest(registration),
    now,
    now,
  );
  if (causal) {
    const exposurePlan = {
      schema_version: "trace.experiment-exposure-plan.v1",
      experiment_id: "experiment-1",
      account_id: ACCOUNT.account_id,
      account_revision: 1,
      profile_id: "profile-1",
      threads_user_id_snapshot: "threads-1",
      username_snapshot: "trace",
      timezone_snapshot: "Asia/Seoul",
      morning_time_snapshot: "07:30",
      evening_time_snapshot: "19:30",
      created_at: now,
    };
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_experiment_exposure_plans
        (experiment_id, account_id, profile_id, threads_user_id_snapshot,
         username_snapshot, timezone_snapshot, morning_time_snapshot,
         evening_time_snapshot, account_revision, plan_json, plan_sha256, created_at)
       VALUES ('experiment-1', 'trace_kr', 'profile-1', 'threads-1', 'trace',
               'Asia/Seoul', '07:30', '19:30', 1, ?, ?, ?)`,
    ).run(canonicalJson(exposurePlan), digest(exposurePlan), now);
  }
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
       VALUES (?, 'plan-1', 'campaign-1', 'experiment-1', ?, 'native_sequence', ?, ?, ?)`,
    ).run(
      treatment.treatment_id,
      treatment.hypothesis_id,
      canonicalJson(treatment),
      digest(treatment),
      now,
    );
    for (const request of treatment.artifact_requests) {
      const bindingSha256 = request.capability_id === "copy.text"
        ? copyBindingSha256
        : captureBindingSha256;
      DB.sqlite.prepare(
        `INSERT INTO hosted_marketing_artifact_requests
          (request_id, campaign_id, treatment_id, capability_id, proof_kind,
           request_json, request_sha256, capability_binding_sha256, state, created_at, updated_at)
         VALUES (?, 'campaign-1', ?, ?, ?, ?, ?, ?, 'approved', ?, ?)`,
      ).run(
        request.request_id,
        treatment.treatment_id,
        request.capability_id,
        request.proof_kind,
        canonicalJson(request),
        digest(request),
        bindingSha256,
        now,
        now,
      );
    }
  }
  return { packet, registration, copyBindingSha256 };
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

async function materializeOne(
  DB,
  projectionRevision,
  { legacyTask = false, legacyOutput = false } = {},
) {
  const requested = await requestCandidateMaterialization(
    { DB },
    ACCOUNT,
    "campaign-1",
    { projection_revision: projectionRevision },
  );
  if (legacyTask) {
    DB.sqlite.prepare(
      "UPDATE hosted_workspace_capture_tasks SET required_capability = NULL WHERE task_id = ?",
    ).run(requested.task_id);
  }
  const task = claimTask(DB, requested.task_id);
  assert.equal(
    task.required_capability,
    legacyTask ? null : "candidate_materialization_v2",
  );
  const payload = JSON.parse(task.task_json).payload;
  const knowledge = DB.sqlite.prepare(
    `SELECT snapshot_json, snapshot_sha256 FROM hosted_marketing_knowledge_snapshots
     WHERE campaign_id = 'campaign-1'`,
  ).get();
  assert.deepEqual(payload.canonical_principles, JSON.parse(knowledge.snapshot_json).principles);
  assert.equal(payload.knowledge_snapshot_sha256, knowledge.snapshot_sha256);
  const candidateSchemaVersion = legacyOutput
    ? "trace.candidate-materialization.v1"
    : "trace.candidate-materialization.v2";
  const candidate = {
    schema_version: candidateSchemaVersion,
    topic: `topic ${requested.assignment_id}`,
    country: "KR",
    caption: `caption ${requested.hypothesis_id}`,
    hypothesis: `hypothesis ${requested.hypothesis_id}`,
    posting_slot: payload.allocation?.posting_slot ?? "morning",
    appium_prompt: "Capture the installed schedule.",
    image_inputs: legacyOutput
      ? {
          trace_items: [
            "08:00 기상",
            "09:00 집중 업무",
            "12:00 점심",
            "15:00 산책",
            "19:00 저녁",
          ],
          device_time: "09:41",
          background_subject: "character_other",
          background_mood: "warm",
          background_search_query: null,
          language: "ko",
        }
      : weeklyImageInputs(),
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
    prompt_version: legacyOutput
      ? "trace.evidence-bound-candidate.v1"
      : "trace.evidence-bound-candidate.v2",
    prompt_sha256: "6".repeat(64),
    output_schema_version: candidateSchemaVersion,
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

function seedCaptureManifests(DB) {
  const rows = DB.sqlite.prepare(
    `SELECT assignment.assignment_id, assignment.campaign_id, assignment.treatment_id,
            request.request_id, request.capability_id, request.request_sha256,
            request.capability_binding_sha256
     FROM hosted_marketing_post_assignments AS assignment
     JOIN hosted_marketing_artifact_requests AS request
       ON request.treatment_id = assignment.treatment_id
      AND request.capability_id = 'capture.native_png'
     LEFT JOIN hosted_marketing_artifact_manifests AS manifest
       ON manifest.assignment_id = assignment.assignment_id
      AND manifest.request_id = request.request_id
     WHERE manifest.manifest_id IS NULL`,
  ).all();
  for (const row of rows) {
    const artifactSha256 = digest({
      assignment_id: row.assignment_id,
      request_id: row.request_id,
    });
    const manifest = {
      schema_version: "trace.artifact-manifest.v1",
      manifest_id: `capture-${row.assignment_id}`,
      campaign_id: row.campaign_id,
      assignment_id: row.assignment_id,
      treatment_id: row.treatment_id,
      request_id: row.request_id,
      capability_id: row.capability_id,
      capability_binding_sha256: row.capability_binding_sha256,
      artifact_uri: `r2:test/${row.assignment_id}.png`,
      artifact_sha256: artifactSha256,
      input_sha256: row.request_sha256,
      execution_id: `capture-task-${row.assignment_id}`,
      claim_ids: ["claim-installed"],
      evidence_ids: [],
      created_at: "2026-09-02T00:00:00Z",
    };
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_artifact_manifests
        (manifest_id, campaign_id, assignment_id, treatment_id, request_id, schema_version,
         manifest_json, manifest_sha256, artifact_uri, artifact_sha256, input_sha256,
         capability_binding_sha256, created_at)
       VALUES (?, ?, ?, ?, ?, 'trace.artifact-manifest.v1', ?, ?, ?, ?, ?, ?, ?)`,
    ).run(
      manifest.manifest_id,
      row.campaign_id,
      row.assignment_id,
      row.treatment_id,
      row.request_id,
      canonicalJson(manifest),
      digest(manifest),
      manifest.artifact_uri,
      artifactSha256,
      row.request_sha256,
      row.capability_binding_sha256,
      manifest.created_at,
    );
  }
}

test("candidate materialization fails closed before reserving work without a v2 worker", async () => {
  const DB = new D1Adapter();
  seedSupervisedCampaign(DB);
  DB.sqlite.prepare(
    `UPDATE mac_workers
     SET capabilities_json = '{"task_kinds":"capture,marketing_judgment"}'
     WHERE worker_id = 'worker-1'`,
  ).run();
  const taskCount = DB.sqlite.prepare(
    "SELECT COUNT(*) AS count FROM hosted_workspace_capture_tasks",
  ).get().count;
  const reservationCount = DB.sqlite.prepare(
    "SELECT COUNT(*) AS count FROM hosted_marketing_materialization_reservations",
  ).get().count;

  await assert.rejects(
    requestCandidateMaterialization(
      { DB },
      ACCOUNT,
      "campaign-1",
      { projection_revision: 4 },
    ),
    (error) => error?.status === 503,
  );
  assert.equal(
    DB.sqlite.prepare("SELECT COUNT(*) AS count FROM hosted_workspace_capture_tasks").get().count,
    taskCount,
  );
  assert.equal(
    DB.sqlite.prepare(
      "SELECT COUNT(*) AS count FROM hosted_marketing_materialization_reservations",
    ).get().count,
    reservationCount,
  );
});

test("an in-flight legacy candidate task can finish with its frozen v1 contract", async () => {
  const DB = new D1Adapter();
  seedSupervisedCampaign(DB);

  const materialized = await materializeOne(
    DB,
    4,
    { legacyTask: true, legacyOutput: true },
  );
  const candidate = DB.sqlite.prepare(
    "SELECT image_inputs_json FROM hosted_workspace_candidates WHERE candidate_id = ?",
  ).get(materialized.candidate_id);
  assert.equal(typeof JSON.parse(candidate.image_inputs_json).trace_items[0], "string");
  const reservation = DB.sqlite.prepare(
    `SELECT state FROM hosted_marketing_materialization_reservations
     WHERE assignment_id = ?`,
  ).get(materialized.assignment_id);
  assert.equal(reservation.state, "completed");
});

test("a v2 candidate task rejects a v1 worker result without mutating campaign state", async () => {
  const DB = new D1Adapter();
  seedSupervisedCampaign(DB);

  await assert.rejects(
    materializeOne(DB, 4, { legacyOutput: true }),
    (error) => error?.status === 409,
  );
  const task = DB.sqlite.prepare(
    `SELECT state, callback_id, required_capability
     FROM hosted_workspace_capture_tasks ORDER BY created_at DESC LIMIT 1`,
  ).get();
  assert.deepEqual({ ...task }, {
    state: "queued",
    callback_id: null,
    required_capability: "candidate_materialization_v2",
  });
  assert.equal(
    DB.sqlite.prepare("SELECT COUNT(*) AS count FROM hosted_workspace_candidates").get().count,
    0,
  );
  assert.equal(
    DB.sqlite.prepare(
      "SELECT state FROM hosted_marketing_materialization_reservations LIMIT 1",
    ).get().state,
    "queued",
  );
});

test("causal experiments materialize only the server-randomized allocation receipt", async () => {
  const DB = new D1Adapter();
  seedSupervisedCampaign(DB, { causal: true });

  await materializeOne(DB, 4);
  await materializeOne(DB, 5);

  const assignments = DB.sqlite.prepare(
    `SELECT assignment_id, assignment_json, allocation_rank
     FROM hosted_marketing_post_assignments
     WHERE experiment_id = 'experiment-1'
     ORDER BY allocation_rank`,
  ).all();
  assert.deepEqual(assignments.map((assignment) => assignment.allocation_rank), [1, 2]);
  const experiment = DB.sqlite.prepare(
    `SELECT allocation_method, randomization_seed_sha256
     FROM hosted_marketing_experiments WHERE experiment_id = 'experiment-1'`,
  ).get();
  for (const assignment of assignments) {
    const allocation = JSON.parse(assignment.assignment_json).allocation;
    assert.deepEqual(allocation, {
      method: "server_randomized_complete_blocks_v1",
      randomization_seed_sha256: experiment.randomization_seed_sha256,
      rank: assignment.allocation_rank,
      posting_slot: assignment.allocation_rank === 1 ? "morning" : "evening",
    });
  }

  const tampered = JSON.parse(assignments[0].assignment_json);
  const tamperedRank = assignments[0].allocation_rank === 1 ? 2 : 1;
  tampered.allocation.rank = tamperedRank;
  DB.sqlite.prepare(
    `UPDATE hosted_marketing_post_assignments
     SET allocation_rank = ?, assignment_json = ? WHERE assignment_id = ?`,
  ).run(tamperedRank, canonicalJson(tampered), assignments[0].assignment_id);
  await assert.rejects(
    requestExperimentEvaluation({ DB }, ACCOUNT, "campaign-1", { projection_revision: 6 }),
    (error) => error.status === 409,
  );
});

test("causal image approval commits the complete exposure schedule before publication", async () => {
  const DB = new D1Adapter();
  seedSupervisedCampaign(DB, { causal: true });
  for (let revision = 4; revision < 8; revision += 1) {
    await materializeOne(DB, revision);
  }
  seedCaptureManifests(DB);
  DB.sqlite.exec(`
    UPDATE hosted_workspace_accounts
    SET threads_auto_publish_enabled = 1, timezone = 'America/New_York',
        morning_time = '06:00', evening_time = '18:00', revision = revision + 1;
    UPDATE hosted_workspace_candidates
    SET status = 'image_awaiting_review', image_key = 'image-key',
        image_sha256 = '${"8".repeat(64)}';
  `);
  const candidate = DB.sqlite.prepare(
    `SELECT candidate_id, revision FROM hosted_workspace_candidates
     WHERE marketing_assignment_id IS NOT NULL ORDER BY candidate_id LIMIT 1`,
  ).get();
  const response = await handleHostedWorkspace(
    new Request(`https://workspace.example/api/candidates/${candidate.candidate_id}/review-image`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        accepted: true,
        expected_revision: candidate.revision,
        rating: 5,
        tags: [],
      }),
    }),
    {
      DB,
      HOSTED_WORKSPACE_ACCOUNT_ID: "trace_kr",
      ARTIFACTS: { delete: async () => {} },
    },
    "context",
  );
  assert.equal(response.status, 200, await response.text());
  const slots = DB.sqlite.prepare(
    `SELECT slot.assignment_id, slot.allocation_rank, slot.posting_slot,
            slot.scheduled_at, slot.commitment_json, slot.commitment_sha256
     FROM hosted_marketing_exposure_slots AS slot ORDER BY slot.scheduled_at`,
  ).all();
  assert.equal(slots.length, 4);
  assert.deepEqual(
    slots.map((slot) => slot.posting_slot),
    ["morning", "evening", "morning", "evening"],
  );
  for (const slot of slots) {
    assert.equal(digest(JSON.parse(slot.commitment_json)), slot.commitment_sha256);
  }
  const publication = DB.sqlite.prepare(
    `SELECT publication.scheduled_at, publication.posting_slot_snapshot,
            publication.timezone_snapshot, publication.wall_clock_snapshot,
            slot.scheduled_at AS committed_at, slot.posting_slot AS committed_slot
     FROM hosted_threads_publications AS publication
     JOIN hosted_marketing_exposure_slots AS slot
       ON slot.assignment_id = publication.marketing_assignment_id`,
  ).get();
  assert.equal(publication.scheduled_at, publication.committed_at);
  assert.equal(publication.posting_slot_snapshot, publication.committed_slot);
  assert.equal(publication.timezone_snapshot, "Asia/Seoul");
  assert.equal(JSON.parse(publication.wall_clock_snapshot).timezone, "Asia/Seoul");
});

test("causal candidate rejects a profile switch after allocation", async () => {
  const DB = new D1Adapter();
  seedSupervisedCampaign(DB, { causal: true });
  const materialized = await materializeOne(DB, 4);
  DB.sqlite.prepare(
    `INSERT INTO hosted_threads_profiles
      (profile_id, account_id, threads_user_id, username, scopes_json,
       token_ciphertext, token_nonce, token_key_version, state, created_at, updated_at)
     VALUES ('profile-2', 'trace_kr', 'threads-2', 'trace_alt', '["threads_basic"]',
             X'03', X'04', 'v1', 'active', 'now', 'now')`,
  ).run();
  const candidate = DB.sqlite.prepare(
    `SELECT candidate_id, revision, threads_profile_id FROM hosted_workspace_candidates
     WHERE candidate_id = ?`,
  ).get(materialized.candidate_id);
  assert.equal(candidate.threads_profile_id, "profile-1");
  const response = await handleHostedWorkspace(
    new Request(
      `https://workspace.example/api/candidates/${candidate.candidate_id}/threads-profile`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: "Bearer control-token",
        },
        body: JSON.stringify({
          threads_profile_id: "profile-2",
          expected_revision: candidate.revision,
        }),
      },
    ),
    {
      DB,
      HOSTED_WORKSPACE_ACCOUNT_ID: "trace_kr",
      CONTROL_PLANE_TOKEN: "control-token",
    },
    "context",
  );
  const responseBody = await response.text();
  assert.equal(response.status, 409, responseBody);
  const after = DB.sqlite.prepare(
    "SELECT threads_profile_id FROM hosted_workspace_candidates WHERE candidate_id = ?",
  ).get(candidate.candidate_id);
  assert.equal(after.threads_profile_id, "profile-1");
});

test("materialized workspace treatment reaches the approved native capture queue", async () => {
  const DB = new D1Adapter();
  seedSupervisedCampaign(DB, { causal: true });
  const materialized = await materializeOne(DB, 4);
  let candidate = DB.sqlite.prepare(
    "SELECT candidate_id, revision, status FROM hosted_workspace_candidates WHERE candidate_id = ?",
  ).get(materialized.candidate_id);
  const reviewed = await handleHostedWorkspace(
    new Request(`https://workspace.example/api/candidates/${candidate.candidate_id}/review`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        accepted: true,
        expected_revision: candidate.revision,
        rating: 5,
        tags: [],
      }),
    }),
    { DB, HOSTED_WORKSPACE_ACCOUNT_ID: "trace_kr" },
    "context",
  );
  assert.equal(reviewed.status, 200, await reviewed.text());
  candidate = DB.sqlite.prepare(
    "SELECT candidate_id, revision, status FROM hosted_workspace_candidates WHERE candidate_id = ?",
  ).get(materialized.candidate_id);
  assert.equal(candidate.status, "caption_approved");

  const queued = await handleHostedWorkspace(
    new Request(
      `https://workspace.example/api/candidates/${candidate.candidate_id}/generate-image`,
      { method: "POST" },
    ),
    { DB, HOSTED_WORKSPACE_ACCOUNT_ID: "trace_kr" },
    "context",
  );
  assert.equal(queued.status, 201, await queued.text());
  const captureTask = DB.sqlite.prepare(
    `SELECT kind, state, created_at FROM hosted_workspace_capture_tasks
     WHERE candidate_id = ? ORDER BY created_at DESC LIMIT 1`,
  ).get(candidate.candidate_id);
  assert.deepEqual({ kind: captureTask.kind, state: captureTask.state }, {
    kind: "capture",
    state: "queued",
  });
  const request = DB.sqlite.prepare(
    `SELECT state FROM hosted_marketing_artifact_requests
     WHERE treatment_id = (SELECT marketing_treatment_id FROM hosted_workspace_candidates
                            WHERE candidate_id = ?)
       AND capability_id = 'capture.native_png'`,
  ).get(candidate.candidate_id);
  assert.equal(request.state, "approved");
});

test("causal evaluation opens only for publications matching immutable exposure commitments", async () => {
  const DB = new D1Adapter();
  const { registration } = seedSupervisedCampaign(DB, { causal: true });
  for (let revision = 4; revision < 8; revision += 1) {
    await materializeOne(DB, revision);
  }
  seedCaptureManifests(DB);
  const exposurePlan = DB.sqlite.prepare(
    `SELECT plan_json, plan_sha256 FROM hosted_marketing_experiment_exposure_plans
     WHERE experiment_id = 'experiment-1'`,
  ).get();
  const experiment = DB.sqlite.prepare(
    `SELECT randomization_seed_sha256 FROM hosted_marketing_experiments
     WHERE experiment_id = 'experiment-1'`,
  ).get();
  const assignments = DB.sqlite.prepare(
    `SELECT assignment_id, candidate_id, eligible_block_id, hypothesis_id, allocation_rank
     FROM hosted_marketing_post_assignments ORDER BY eligible_block_id, allocation_rank`,
  ).all();
  const baseTime = Date.now() - 120 * 60 * 60 * 1000;
  for (const [index, assignment] of assignments.entries()) {
    const postingSlot = assignment.allocation_rank === 1 ? "morning" : "evening";
    const scheduledAt = new Date(baseTime + index * 60 * 60 * 1000).toISOString();
    const wallClock = { timezone: "Asia/Seoul", time: postingSlot === "morning" ? "07:30" : "19:30" };
    const commitment = {
      schema_version: "trace.exposure-slot.v1",
      experiment_id: "experiment-1",
      assignment_id: assignment.assignment_id,
      eligible_block_id: assignment.eligible_block_id,
      hypothesis_id: assignment.hypothesis_id,
      allocation_rank: assignment.allocation_rank,
      randomization_seed_sha256: experiment.randomization_seed_sha256,
      posting_slot: postingSlot,
      exposure_plan_sha256: exposurePlan.plan_sha256,
      profile_id_snapshot: "profile-1",
      threads_user_id_snapshot: "threads-1",
      username_snapshot: "trace",
      timezone_snapshot: "Asia/Seoul",
      wall_clock_snapshot: wallClock,
      scheduled_at: scheduledAt,
      tolerance_seconds: 1800,
    };
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_exposure_slots
        (slot_id, campaign_id, experiment_id, assignment_id, eligible_block_id,
         hypothesis_id, allocation_rank, posting_slot, timezone_snapshot,
         exposure_plan_sha256, profile_id_snapshot, threads_user_id_snapshot,
         username_snapshot, wall_clock_snapshot, scheduled_at, tolerance_seconds,
         commitment_json, commitment_sha256, created_at)
       VALUES (?, 'campaign-1', 'experiment-1', ?, ?, ?, ?, ?, 'Asia/Seoul',
               ?, 'profile-1', 'threads-1', 'trace', ?, ?, 1800, ?, ?, ?)`,
    ).run(
      `slot-${index}`,
      assignment.assignment_id,
      assignment.eligible_block_id,
      assignment.hypothesis_id,
      assignment.allocation_rank,
      postingSlot,
      exposurePlan.plan_sha256,
      JSON.stringify(wallClock),
      scheduledAt,
      canonicalJson(commitment),
      digest(commitment),
      scheduledAt,
    );
    const link = await createVariantLink(
      { DB },
      ACCOUNT,
      "campaign-1",
      assignment.assignment_id,
      {
        destination_uri: "https://trace.example/setup",
        expires_at: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(),
      },
    );
    DB.sqlite.prepare(
      `INSERT INTO hosted_threads_publications
        (publication_id, account_id, candidate_id, candidate_revision, profile_id,
         threads_user_id_snapshot, username_snapshot, state, caption_snapshot,
         image_key_snapshot, image_sha256_snapshot,
         timezone_snapshot, posting_slot_snapshot, wall_clock_snapshot, scheduled_at,
         threads_post_id, permalink, published_at, created_at, updated_at,
         marketing_assignment_id)
       VALUES (?, 'trace_kr', ?, 2, 'profile-1', 'threads-1', 'trace',
               'published', 'caption', 'image-key', ?,
               'Asia/Seoul', ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(
      `publication-${index}`,
      assignment.candidate_id,
      "8".repeat(64),
      postingSlot,
      JSON.stringify(wallClock),
      scheduledAt,
      `post-${index}`,
      `https://threads.net/post-${index}`,
      scheduledAt,
      scheduledAt,
      scheduledAt,
      assignment.assignment_id,
    );
    if (assignment.hypothesis_id === "challenger") {
      await ingestProductEvent(
        { DB, TRACE_EVENT_HASH_SALT: "test-salt" },
        {
          event_id: `causal-event-${index}`,
          event_version: "trace.product-event.v1",
          event_type: "setup_completed",
          variant_token: link.token,
          install_id: `causal-install-${index}`,
          occurred_at: new Date(Date.parse(scheduledAt) + 60 * 60 * 1000).toISOString(),
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
  assert.equal(request.causal_exposure_verified, true);
  const evaluation = deriveExperimentEvaluation(request);
  assert.equal(evaluation.outcome_scope, "estimated_treatment_effect");
  assert.equal(evaluation.eligible_blocks, 2);
  assert.equal(evaluation.causal_estimate.treatment_minus_control_basis_points, 10000);
  assert.equal(evaluation.state, "inconclusive");
  assert.equal(evaluation.experiment_id, registration.experiment_id);

  DB.sqlite.prepare(
    "DELETE FROM hosted_workspace_capture_tasks WHERE task_id = ?",
  ).run(requested.task_id);
  const identityPublication = DB.sqlite.prepare(
    "SELECT publication_id FROM hosted_threads_publications ORDER BY publication_id LIMIT 1",
  ).get();
  DB.sqlite.prepare(
    "UPDATE hosted_threads_publications SET threads_user_id_snapshot = 'threads-other' WHERE publication_id = ?",
  ).run(identityPublication.publication_id);
  const identityMismatch = await requestExperimentEvaluation(
    { DB },
    ACCOUNT,
    "campaign-1",
    { projection_revision: 8 },
  );
  const identityTask = claimTask(DB, identityMismatch.task_id);
  assert.equal(JSON.parse(identityTask.task_json).payload.request.causal_exposure_verified, false);
  DB.sqlite.prepare(
    "DELETE FROM hosted_workspace_capture_tasks WHERE task_id = ?",
  ).run(identityMismatch.task_id);
  DB.sqlite.prepare(
    "UPDATE hosted_threads_publications SET threads_user_id_snapshot = 'threads-1' WHERE publication_id = ?",
  ).run(identityPublication.publication_id);
  const deviated = DB.sqlite.prepare(
    `SELECT publication_id, scheduled_at FROM hosted_threads_publications
     ORDER BY publication_id LIMIT 1`,
  ).get();
  DB.sqlite.prepare(
    `UPDATE hosted_threads_publications SET published_at = ? WHERE publication_id = ?`,
  ).run(
    new Date(Date.parse(deviated.scheduled_at) + 31 * 60 * 1000).toISOString(),
    deviated.publication_id,
  );
  const retried = await requestExperimentEvaluation(
    { DB },
    ACCOUNT,
    "campaign-1",
    { projection_revision: 8 },
  );
  const retriedTask = claimTask(DB, retried.task_id);
  assert.equal(
    JSON.parse(retriedTask.task_json).payload.request.causal_exposure_verified,
    false,
  );
});

test("assisted loop materializes balanced blocks and evaluates attributed outcomes", async () => {
  const DB = new D1Adapter();
  const { packet, registration } = seedSupervisedCampaign(DB);
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
  seedCaptureManifests(DB);
  assert.equal(
    DB.sqlite.prepare("SELECT COUNT(*) AS count FROM hosted_marketing_artifact_manifests").get().count,
    8,
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
    causal_estimate: null,
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
  const queuedReassessment = DB.sqlite.prepare(
    `SELECT * FROM hosted_workspace_capture_tasks
     WHERE json_extract(task_json, '$.payload.judgment') = 'outcome_reassessment'
       AND json_extract(task_json, '$.payload.campaign_id') = 'campaign-1'`,
  ).get();
  assert.ok(queuedReassessment);
  const reassessmentTask = claimTask(DB, queuedReassessment.task_id);
  const reassessmentPayload = JSON.parse(reassessmentTask.task_json).payload;
  assert.equal(reassessmentPayload.situation, "experiment_result");
  assert.equal(reassessmentPayload.evaluation_sha256, digest(evaluation));
  const reassessment = {
    schema_version: "trace.marketing-reassessment.v1",
    reassessment_id: reassessmentPayload.reassessment_id,
    campaign_id: "campaign-1",
    trigger_evaluation_id: evaluation.evaluation_id,
    trigger_evaluation_sha256: digest(evaluation),
    situation: "experiment_result",
    decision_dossier: {
      schema_version: "trace.marketing-decision-dossier.v1",
      situation: "experiment_result",
      selected_icp_id: "research_needed",
      selection_basis_ids: [],
      positioning: reassessmentPayload.prior_strategy.decision_dossier.positioning,
      evidence_dispositions: [
        reassessmentPayload.prior_strategy.decision_dossier.evidence_dispositions[0],
        {
          evidence_id: evaluation.evaluation_id,
          disposition: "supports",
          confidence_basis_points: 10000,
          freshness: "fresh",
          use: "use_as_constraint",
          reason: "The server-derived evaluation is the newest outcome signal.",
        },
      ],
      recommended_next_step: "research",
      reason: "The result changes the content hypothesis but still does not identify an ICP.",
      required_proof_ids: ["claim-installed", evaluation.evaluation_id],
    },
    hypothesis_reassessments: [
      {
        hypothesis_id: "control",
        disposition: "retain",
        rationale: "Keep the stable baseline for the next registered comparison.",
        next_test: "Repeat the control without changing the proof.",
      },
      {
        hypothesis_id: "challenger",
        disposition: "revise",
        rationale: "The attributed result warrants a narrower replication.",
        next_test: "Change only the opening value frame.",
      },
    ],
    unanswered_questions: ["Will this direction replicate in another complete block?"],
    created_at: evaluation.evaluated_at,
  };
  const reassessmentCallback = {
    callback_id: `${reassessmentTask.task_id}:completed`,
    task_id: reassessmentTask.task_id,
    run_id: reassessmentTask.run_id,
    account_id: reassessmentTask.account_id,
    kind: "marketing_judgment",
    result: {
      status: "succeeded",
      output: {
        pipeline: "hosted_marketing_judgment_v1",
        judgment: "outcome_reassessment",
        reassessment,
        reassessment_sha256: digest(reassessment),
        tool_actions_created: 0,
      },
    },
  };
  const reassessmentAccepted = await receiveHostedOutcomeReassessmentCallback(
    { DB },
    reassessmentTask,
    reassessmentCallback,
    { worker_id: "worker-1" },
  );
  assert.equal(reassessmentAccepted.state, "proposed");
  const storedReassessment = DB.sqlite.prepare(
    `SELECT situation, state FROM hosted_marketing_outcome_reassessments
     WHERE campaign_id = 'campaign-1'`,
  ).get();
  assert.equal(storedReassessment.situation, "experiment_result");
  assert.equal(storedReassessment.state, "proposed");

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
  const tasksBeforeMismatchedLineages = DB.sqlite.prepare(
    "SELECT COUNT(*) AS count FROM hosted_workspace_capture_tasks",
  ).get().count;
  DB.sqlite.prepare(
    "UPDATE hosted_marketing_campaigns SET mode = 'shadow' WHERE campaign_id = 'campaign-2'",
  ).run();
  await assert.rejects(
    requestLearningSynthesis(
      { DB },
      ACCOUNT,
      {
        learning_id: "learning-mismatched-selector",
        evaluation_ids: [requested.evaluation_id, secondEvaluation.evaluation_id],
      },
    ),
    (error) => error.status === 409,
  );
  assert.equal(
    DB.sqlite.prepare(
      "SELECT COUNT(*) AS count FROM hosted_workspace_capture_tasks",
    ).get().count,
    tasksBeforeMismatchedLineages,
  );
  DB.sqlite.prepare(
    "UPDATE hosted_marketing_campaigns SET mode = 'assisted' WHERE campaign_id = 'campaign-2'",
  ).run();
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
    applicability: learningPayload.applicability,
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
  const learningMutationSnapshot = () => ({
    candidates: DB.sqlite.prepare(
      "SELECT COUNT(*) AS count FROM hosted_marketing_learning_candidates",
    ).get().count,
    campaign: DB.sqlite.prepare(
      `SELECT state, projection_revision FROM hosted_marketing_campaigns
       WHERE campaign_id = 'campaign-2'`,
    ).get(),
    task: DB.sqlite.prepare(
      `SELECT callback_id, callback_reservation_id, result_json
       FROM hosted_workspace_capture_tasks WHERE task_id = ?`,
    ).get(learningTask.task_id),
  });
  const beforeRejectedLearning = learningMutationSnapshot();
  const forgedApplicability = structuredClone(learningCallback);
  forgedApplicability.result.output.learning_candidate.applicability = {
    ...learningPayload.applicability,
    feature_id: "trace.lockscreen.unknown",
  };
  forgedApplicability.result.output.learning_candidate_sha256 = digest(
    forgedApplicability.result.output.learning_candidate,
  );
  await assert.rejects(
    receiveHostedLearningSynthesisCallback(
      { DB },
      learningTask,
      forgedApplicability,
      { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );
  assert.deepEqual(learningMutationSnapshot(), beforeRejectedLearning);
  const missingApplicability = structuredClone(learningCallback);
  delete missingApplicability.result.output.learning_candidate.applicability;
  missingApplicability.result.output.learning_candidate_sha256 = digest(
    missingApplicability.result.output.learning_candidate,
  );
  await assert.rejects(
    receiveHostedLearningSynthesisCallback(
      { DB },
      learningTask,
      missingApplicability,
      { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );
  assert.deepEqual(learningMutationSnapshot(), beforeRejectedLearning);
  DB.sqlite.prepare(
    "UPDATE hosted_marketing_campaigns SET mode = 'shadow' WHERE campaign_id = 'campaign-2'",
  ).run();
  await assert.rejects(
    receiveHostedLearningSynthesisCallback(
      { DB },
      learningTask,
      learningCallback,
      { worker_id: "worker-1" },
    ),
    (error) => error.status === 409,
  );
  assert.deepEqual(learningMutationSnapshot(), beforeRejectedLearning);
  DB.sqlite.prepare(
    "UPDATE hosted_marketing_campaigns SET mode = 'assisted' WHERE campaign_id = 'campaign-2'",
  ).run();
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
  assert.deepEqual(
    JSON.parse(DB.sqlite.prepare(
      "SELECT principle_json FROM hosted_marketing_principles WHERE principle_id = ?",
    ).get(approval.principle_id).principle_json).applicability,
    learningPayload.applicability,
  );
  const learningGrant = DB.sqlite.prepare(
    `SELECT grant_id FROM hosted_marketing_approval_grants
     WHERE target_kind = 'learning_candidate' AND target_id = 'learning-1'`,
  ).get();
  const legacyPrinciple = {
    schema_version: "trace.marketing-principle.v1",
    principle_id: "principle-legacy-without-applicability",
    learning_id: "learning-1",
    statement: "This legacy principle must not auto-apply.",
    scope: "KR",
    independent_lineage_ids: learningCandidate.independent_lineage_ids,
    state: "provisional",
    created_at: secondEvaluatedAt,
  };
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_principles
      (principle_id, learning_id, approval_grant_id, principle_json,
       principle_sha256, state, created_at, updated_at)
     VALUES (?, 'learning-1', ?, ?, ?, 'provisional', ?, ?)`,
  ).run(
    legacyPrinciple.principle_id,
    learningGrant.grant_id,
    canonicalJson(legacyPrinciple),
    digest(legacyPrinciple),
    secondEvaluatedAt,
    secondEvaluatedAt,
  );
  for (let index = 0; index < 100; index += 1) {
    const nonmatchingPrinciple = {
      ...legacyPrinciple,
      principle_id: `principle-000-nonmatching-${index}`,
      statement: `Nonmatching principle ${index}`,
      applicability: { ...learningPayload.applicability, mode: "shadow" },
    };
    DB.sqlite.prepare(
      `INSERT INTO hosted_marketing_principles
        (principle_id, learning_id, approval_grant_id, principle_json,
         principle_sha256, state, created_at, updated_at)
       VALUES (?, 'learning-1', ?, ?, ?, 'provisional', ?, ?)`,
    ).run(
      nonmatchingPrinciple.principle_id,
      learningGrant.grant_id,
      canonicalJson(nonmatchingPrinciple),
      digest(nonmatchingPrinciple),
      secondEvaluatedAt,
      secondEvaluatedAt,
    );
  }

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
    !JSON.parse(nextTask.task_json).payload.canonical_principles.includes(
      learningCandidate.statement,
    ),
  );
  const legacyCandidate = {
    schema_version: "trace.learning-candidate.v1",
    learning_id: "learning-legacy",
    campaign_id: "campaign-next",
    statement: "Legacy candidate must not be promoted.",
    scope: "KR",
    independent_lineage_ids: learningCandidate.independent_lineage_ids,
    status: "candidate",
    created_at: secondEvaluatedAt,
  };
  const legacyCandidateSha = digest(legacyCandidate);
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_learning_candidates
      (learning_id, campaign_id, schema_version, candidate_json, candidate_sha256,
       state, created_at, updated_at)
     VALUES (?, 'campaign-next', 'trace.learning-candidate.v1', ?, ?, 'candidate', ?, ?)`,
  ).run(
    legacyCandidate.learning_id,
    canonicalJson(legacyCandidate),
    legacyCandidateSha,
    secondEvaluatedAt,
    secondEvaluatedAt,
  );
  await assert.rejects(
    decideLearningCandidate({ DB }, ACCOUNT, legacyCandidate.learning_id, {
      candidate_sha256: legacyCandidateSha,
      reviewer_id: "reviewer-legacy",
      decision: "approved",
    }),
    (error) => error.status === 409,
  );
  assert.equal(
    DB.sqlite.prepare(
      "SELECT state FROM hosted_marketing_learning_candidates WHERE learning_id = 'learning-legacy'",
    ).get().state,
    "candidate",
  );
  assert.equal(
    DB.sqlite.prepare(
      `SELECT COUNT(*) AS count FROM hosted_marketing_approval_grants
       WHERE target_kind = 'learning_candidate' AND target_id = 'learning-legacy'`,
    ).get().count,
    0,
  );

  const applicablePacket = structuredClone(packet);
  const applicableCampaign = await createShadowCampaign({ DB }, ACCOUNT, {
    campaign_id: "campaign-applicable",
    business_outcome: "Replicate the bounded installed-evidence format.",
    current_control: "아이폰 쓰는 유저들...",
    feature_packet: applicablePacket,
    mode: "assisted",
    origin_campaign_id: "origin-1",
    product_truth_review: {
      decision: "approved",
      approved_claim_ids: ["claim-installed"],
      reviewer_id: "reviewer-3",
      reviewed_at: new Date().toISOString(),
    },
    research_enabled: false,
  });
  const applicableTask = DB.sqlite.prepare(
    "SELECT task_json FROM hosted_workspace_capture_tasks WHERE task_id = ?",
  ).get(applicableCampaign.task_id);
  assert.ok(
    JSON.parse(applicableTask.task_json).payload.canonical_principles.includes(
      learningCandidate.statement,
    ),
  );
  assert.ok(
    !JSON.parse(applicableTask.task_json).payload.canonical_principles.includes(
      legacyPrinciple.statement,
    ),
  );
});

test("review queue exposes one exact, read-only decision packet without customer source data", async () => {
  const DB = new D1Adapter();
  const seeded = seedSupervisedCampaign(DB, { reviewContext: true });
  await assert.rejects(
    marketingReviewPacket({ DB }, ACCOUNT.account_id, "campaign-1"),
    (error) => error.status === 409,
  );
  const snapshotSha256 = "f".repeat(64);
  DB.sqlite.exec(`
    DELETE FROM hosted_marketing_approval_grants WHERE grant_id = 'creative-grant-1';
    UPDATE hosted_marketing_media_plans SET state = 'proposed' WHERE plan_id = 'plan-1';
  `);
  const privateArtifactUri = "https://assets.example/review.png?sig=review-secret";
  const manifest = {
    schema_version: "trace.artifact-manifest.v1",
    manifest_id: "manifest-review-1",
    capability_id: "copy.text",
    capability_binding_sha256: seeded.copyBindingSha256,
    artifact_uri: privateArtifactUri,
  };
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_artifact_manifests
      (manifest_id, campaign_id, treatment_id, request_id, schema_version, manifest_json,
       manifest_sha256, artifact_uri, artifact_sha256, input_sha256, capability_binding_sha256,
       created_at)
     VALUES ('manifest-review-1', 'campaign-1', 'treatment-control', 'copy-control',
             'trace.artifact-manifest.v1', ?, ?, ?, ?, ?, ?, '2026-09-02T00:00:00Z')`,
  ).run(
    canonicalJson(manifest),
    digest(manifest),
    privateArtifactUri,
    "1".repeat(64),
    "2".repeat(64),
    seeded.copyBindingSha256,
  );
  const readSnapshot = () => ({
    grants: DB.sqlite.prepare(
      "SELECT COUNT(*) AS count FROM hosted_marketing_approval_grants",
    ).get().count,
    events: DB.sqlite.prepare(
      "SELECT COUNT(*) AS count FROM hosted_marketing_run_events WHERE campaign_id = 'campaign-1'",
    ).get().count,
    tasks: DB.sqlite.prepare(
      "SELECT COUNT(*) AS count FROM hosted_workspace_capture_tasks WHERE account_id = 'trace_kr'",
    ).get().count,
    campaign: DB.sqlite.prepare(
      `SELECT state, projection_revision FROM hosted_marketing_campaigns
       WHERE campaign_id = 'campaign-1'`,
    ).get(),
    plan: DB.sqlite.prepare(
      "SELECT state FROM hosted_marketing_media_plans WHERE plan_id = 'plan-1'",
    ).get(),
  });
  const beforeRead = readSnapshot();
  const creativeQueue = await listMarketingReviewQueue({ DB }, ACCOUNT.account_id);
  const planSha256 = DB.sqlite.prepare(
    "SELECT plan_sha256 FROM hosted_marketing_media_plans WHERE plan_id = 'plan-1'",
  ).get().plan_sha256;
  assert.equal(creativeQueue.schema_version, "trace.marketing-review-queue.v1");
  assert.equal(creativeQueue.items.length, 1);
  assert.deepEqual(creativeQueue.items[0].target, {
    kind: "media_plan",
    id: "plan-1",
    sha256: planSha256,
  });
  assert.equal(creativeQueue.items[0].approval.action.body.media_plan_id, "plan-1");
  assert.equal(creativeQueue.items[0].approval.action.body.projection_revision, 4);
  const creativePacket = await marketingReviewPacket({ DB }, ACCOUNT.account_id, "campaign-1");
  assert.equal(creativePacket.approval.scope, "creative");
  assert.equal(creativePacket.approval.target_id, "plan-1");
  assert.equal(creativePacket.effect.external_side_effect, false);
  assert.deepEqual(creativePacket.campaign.marketing_context_snapshot, {
    snapshot_id: "review-context-1",
    sha256: snapshotSha256,
  });
  assert.ok(!JSON.stringify(creativePacket).includes("private customer source"));
  assert.deepEqual(creativePacket.creative.artifact_manifests[0], {
    manifest_id: "manifest-review-1",
    treatment_id: "treatment-control",
      request_id: "copy-control",
    sha256: digest(manifest),
    artifact_sha256: "1".repeat(64),
    input_sha256: "2".repeat(64),
    capability_binding_sha256: seeded.copyBindingSha256,
    capture_provenance: null,
    created_at: "2026-09-02T00:00:00Z",
  });
  assert.ok(!JSON.stringify(creativePacket).includes(privateArtifactUri));
  assert.ok(!JSON.stringify(creativePacket).includes("review-secret"));
  assert.deepEqual(readSnapshot(), beforeRead);

  const unauthorized = await handleHostedMarketingAgent(
    new Request("https://workspace.example/api/marketing-agent/review-queue"),
    { DB, CONTROL_PLANE_TOKEN: "secret" },
    ACCOUNT,
  );
  assert.equal(unauthorized.status, 401);
  const unauthorizedPacket = await handleHostedMarketingAgent(
    new Request("https://workspace.example/api/marketing-agent/campaigns/campaign-1/review-packet"),
    { DB, CONTROL_PLANE_TOKEN: "secret" },
    ACCOUNT,
  );
  assert.equal(unauthorizedPacket.status, 401);
  const authorizedQueue = await handleHostedMarketingAgent(
    new Request("https://workspace.example/api/marketing-agent/review-queue", {
      headers: { authorization: "Bearer secret" },
    }),
    { DB, CONTROL_PLANE_TOKEN: "secret" },
    ACCOUNT,
  );
  assert.equal(authorizedQueue.status, 200);
  assert.equal((await authorizedQueue.json()).items[0].review_kind, "creative");
  const authorizedPacket = await handleHostedMarketingAgent(
    new Request("https://workspace.example/api/marketing-agent/campaigns/campaign-1/review-packet", {
      headers: { authorization: "Bearer secret" },
    }),
    { DB, CONTROL_PLANE_TOKEN: "secret" },
    ACCOUNT,
  );
  assert.equal(authorizedPacket.status, 200);
  assert.equal((await authorizedPacket.json()).approval.target_kind, "media_plan");
  assert.deepEqual(readSnapshot(), beforeRead);

  DB.sqlite.prepare(
    "UPDATE hosted_marketing_media_plans SET state = 'stale' WHERE plan_id = 'plan-1'",
  ).run();
  const beforeStaleApproval = readSnapshot();
  const staleApproval = await handleHostedMarketingAgent(
    new Request("https://workspace.example/api/marketing-agent/campaigns/campaign-1/media-approval", {
      method: "POST",
      headers: {
        authorization: "Bearer secret",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        ...creativeQueue.items[0].approval.action.body,
        reviewer_id: "reviewer-stale",
        decision: "approved",
      }),
    }),
    { DB, CONTROL_PLANE_TOKEN: "secret" },
    ACCOUNT,
  );
  assert.equal(staleApproval.status, 409);
  assert.deepEqual(readSnapshot(), beforeStaleApproval);

  DB.sqlite.exec(`
    UPDATE hosted_marketing_media_plans SET state = 'approved' WHERE plan_id = 'plan-1';
    UPDATE hosted_marketing_campaigns SET state = 'experiment_registered' WHERE campaign_id = 'campaign-1';
  `);
  const strategyQueue = await listMarketingReviewQueue({ DB }, ACCOUNT.account_id);
  assert.equal(strategyQueue.items[0].review_kind, "strategy");
  const strategyPacket = await marketingReviewPacket({ DB }, ACCOUNT.account_id, "campaign-1");
  assert.equal(strategyPacket.approval.target_kind, "strategy_brief");
  assert.equal(strategyPacket.approval.action.body.strategy_brief_id, "brief-1");

  const learningCandidate = {
    schema_version: "trace.learning-candidate.v1",
    learning_id: "learning-review-1",
    campaign_id: "campaign-1",
    statement: "A bounded candidate learning still needs human promotion.",
    scope: "exact account and feature selector",
    applicability: {
      schema_version: "trace.marketing-learning-applicability.v1",
      account_id: ACCOUNT.account_id,
      feature_id: "trace.lockscreen.ai-concepts",
      feature_packet_sha256: "a".repeat(64),
      country: "KR",
      language: "ko",
      mode: "assisted",
      marketing_context_snapshot_sha256: snapshotSha256,
    },
    independent_lineage_ids: ["evaluation-1", "evaluation-2"],
    status: "candidate",
    created_at: "2026-09-02T00:00:00Z",
  };
  DB.sqlite.prepare(
    `INSERT INTO hosted_marketing_learning_candidates
      (learning_id, campaign_id, schema_version, candidate_json, candidate_sha256,
       state, created_at, updated_at)
     VALUES (?, 'campaign-1', 'trace.learning-candidate.v1', ?, ?, 'candidate', ?, ?)`,
  ).run(
    learningCandidate.learning_id,
    canonicalJson(learningCandidate),
    digest(learningCandidate),
    learningCandidate.created_at,
    learningCandidate.created_at,
  );
  DB.sqlite.prepare(
    "UPDATE hosted_marketing_campaigns SET state = 'learning_candidate' WHERE campaign_id = 'campaign-1'",
  ).run();
  const learningQueue = await listMarketingReviewQueue({ DB }, ACCOUNT.account_id);
  assert.equal(learningQueue.items[0].review_kind, "learning");
  assert.equal(learningQueue.items[0].approval.action.body.candidate_sha256, digest(learningCandidate));
  const learningPacket = await marketingReviewPacket({ DB }, ACCOUNT.account_id, "campaign-1");
  assert.equal(learningPacket.approval.target_kind, "learning_candidate");
  assert.equal(learningPacket.learning.candidates[0].learning_id, "learning-review-1");
});

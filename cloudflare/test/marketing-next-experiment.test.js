import assert from "node:assert/strict";
import test from "node:test";

import { receiveHostedNextExperimentCallback } from "../src/hosted-next-experiment-callback.js";
import { receiveHostedOutcomeReassessmentCallback } from "../src/hosted-outcome-reassessment-callback.js";
import { handleHostedMarketingAgent } from "../src/marketing-agent.js";
import {
  buildNextExperimentRequest,
  buildNextExperimentTask,
  canonicalJson,
  canonicalSha256,
  expectedNextExperimentDraftId,
  InvalidNextExperiment,
  NEXT_EXPERIMENT_CAPABILITY,
  publicNextExperimentSummary,
  runDueNextExperimentRequests,
  validateNextExperimentDraft,
} from "../src/marketing-next-experiment.js";
import { D1Adapter } from "./d1-fixture.js";

const NOW = "2026-09-02T00:00:00.000Z";
const ACCOUNT = "trace_kr";

async function fixture(recommendedNextStep = "design_experiment", withNextTest = true) {
  const featurePacket = {
    schema_version: "trace.feature-evidence.v1",
    packet_id: "packet-1",
    feature_id: "feature-1",
    title: "Dynamic lock screen",
    lifecycle: "source_candidate",
    repository: "corca-ai/trace",
    mutable_ref: "develop",
    resolved_commit_sha: "a".repeat(40),
    tree_sha: "b".repeat(40),
    claims: [{
      claim_id: "claim-1",
      text: "One character changes with the day.",
      status: "source_supported",
      evidence_ids: ["evidence-1"],
    }],
    evidence: [],
    limitations: [],
    gate: { publication_allowed: false, allowed_claim_ids: [], blocked_claim_ids: [], reasons: [] },
    observed_at: NOW,
  };
  const experimentRegistration = {
    experiment_id: "experiment-1",
    manipulated_component: "opening value frame",
    held_constant_components: ["account", "posting slot"],
    allowed_incidental_differences: [],
    activated_hypothesis_ids: ["control", "challenger"],
    primary_outcome: {
      name: "setup_completed",
      scope: "direct_response_attribution",
      window_hours: 48,
      causal_estimand: null,
    },
    diagnostic_metrics: ["reply_rate"],
    guardrails: ["no unsupported claim"],
    minimum_eligible_blocks: 2,
    maximum_posts: 8,
    maximum_duration_hours: 336,
    minimum_attribution_coverage_basis_points: 8000,
    stop_rules: ["claim contradiction"],
    inconclusive_when: ["insufficient blocks"],
  };
  const priorStrategy = {
    schema_version: "trace.strategy-brief.v1",
    brief_id: "brief-1",
    campaign_id: "campaign-1",
    account_id: ACCOUNT,
    feature_packet_id: featurePacket.packet_id,
    feature_packet_sha256: await canonicalSha256(featurePacket),
    context_receipt_sha256: "c".repeat(64),
    business_outcome: "Find a repeatable launch format.",
    audience_situation: "iPhone owners using a static character wallpaper",
    belief_to_change: "A lock screen can feel alive through the day.",
    decision_dossier: {
      schema_version: "trace.marketing-decision-dossier.v1",
      situation: "new_launch",
      selected_icp_id: "character-fans",
      selection_basis_ids: ["signal-1"],
      positioning: {
        category: "dynamic companion",
        current_alternative: "static wallpaper",
        differentiated_mechanism: "scheduled character scenes",
        proof_claim_ids: ["claim-1"],
      },
      evidence_dispositions: [
        {
          evidence_id: "evaluation-1", disposition: "supports",
          confidence_basis_points: 10000, freshness: "fresh", use: "use_as_constraint",
          reason: "The frozen evaluation is the latest signal.",
        },
        {
          evidence_id: "signal-1", disposition: "insufficient",
          confidence_basis_points: 5000, freshness: "fresh", use: "test",
          reason: "The mechanism remains uncertain.",
        },
      ],
      recommended_next_step: "design_experiment",
      reason: "The audience is sufficiently bounded.",
      required_proof_ids: ["claim-1"],
    },
    hypotheses: [
      { hypothesis_id: "control", role: "control", claim_ids: ["claim-1"] },
      { hypothesis_id: "challenger", role: "challenger", claim_ids: ["claim-1"] },
    ],
    experiment: experimentRegistration,
    created_at: NOW,
  };
  const evaluation = {
    schema_version: "trace.experiment-evaluation.v1",
    evaluation_id: "evaluation-1",
    campaign_id: "campaign-1",
    experiment_id: experimentRegistration.experiment_id,
    state: "evaluated",
    winner_hypothesis_id: "challenger",
    guardrail_failures: [],
    interpretation: "The challenger had the highest observed attributed rate.",
    evaluated_at: NOW,
  };
  const evaluationSha256 = await canonicalSha256(evaluation);
  const reassessment = {
    schema_version: "trace.marketing-reassessment.v1",
    reassessment_id: "reassessment-1",
    campaign_id: "campaign-1",
    trigger_evaluation_id: evaluation.evaluation_id,
    trigger_evaluation_sha256: evaluationSha256,
    situation: "experiment_result",
    decision_dossier: {
      schema_version: "trace.marketing-decision-dossier.v1",
      situation: "experiment_result",
      selected_icp_id: "character-fans",
      selection_basis_ids: [evaluation.evaluation_id, "signal-1"],
      positioning: priorStrategy.decision_dossier.positioning,
      evidence_dispositions: [
        {
          evidence_id: "evaluation-1", disposition: "supports",
          confidence_basis_points: 10000, freshness: "fresh", use: "use_as_constraint",
          reason: "The frozen evaluation is the latest signal.",
        },
        {
          evidence_id: "signal-1", disposition: "insufficient",
          confidence_basis_points: 5000, freshness: "fresh", use: "test",
          reason: "The mechanism remains uncertain.",
        },
      ],
      recommended_next_step: recommendedNextStep,
      reason: "Use the observed result as a constraint, not a universal truth.",
      required_proof_ids: ["claim-1", evaluation.evaluation_id],
    },
    hypothesis_reassessments: [
      { hypothesis_id: "control", disposition: "retain", rationale: "Keep baseline.", next_test: null },
      {
        hypothesis_id: "challenger",
        disposition: "revise",
        rationale: "Test the narrower continuity mechanism.",
        next_test: withNextTest ? "Change only the opening frame." : null,
      },
    ],
    unanswered_questions: ["Will this replicate?"],
    created_at: NOW,
  };
  const knowledgeSnapshot = { principles: [] };
  const campaign = {
    campaign_id: "campaign-1",
    account_id: ACCOUNT,
    state: "evaluated",
    business_outcome: priorStrategy.business_outcome,
    feature_packet_id: featurePacket.packet_id,
    marketing_context_snapshot_id: null,
    marketing_context_snapshot_sha256: null,
    agent_run_id: null,
    research_session_id: null,
    research_input_sha256: null,
    research_trace_sha256: null,
    research_continuation_sha256: null,
  };
  const record = await buildNextExperimentRequest({
    accountId: ACCOUNT,
    campaign,
    featurePacket,
    featurePacketSha256: await canonicalSha256(featurePacket),
    priorStrategy,
    priorStrategySha256: await canonicalSha256(priorStrategy),
    experimentRegistration,
    registrationSha256: await canonicalSha256(experimentRegistration),
    evaluation,
    evaluationSha256,
    reassessment,
    reassessmentSha256: await canonicalSha256(reassessment),
    knowledgeSnapshot,
    knowledgeSnapshotSha256: await canonicalSha256(knowledgeSnapshot),
  });
  return { campaign, evaluation, experimentRegistration, featurePacket, knowledgeSnapshot,
    priorStrategy, reassessment, record };
}

async function draft(record) {
  const request = record.request;
  return {
    schema_version: "trace.next-experiment-draft.v1",
    draft_id: await expectedNextExperimentDraftId(request),
    campaign_id: request.campaign_id,
    account_id: request.account_id,
    trigger_evaluation_id: request.evaluation.evaluation_id,
    trigger_evaluation_sha256: request.source_lineage.evaluation_sha256,
    trigger_reassessment_id: request.reassessment.reassessment_id,
    trigger_reassessment_sha256: request.source_lineage.reassessment_sha256,
    prior_strategy_sha256: request.source_lineage.strategy_sha256,
    control_hypothesis_id: "control",
    primary_outcome: request.prior_strategy.experiment.primary_outcome,
    held_constant_components: request.prior_strategy.experiment.held_constant_components,
    source_hypothesis_ids: ["challenger"],
    supporting_claim_ids: ["claim-1"],
    evidence: [
      { evidence_id: "evaluation-1", interpretation: "The challenger led on attributed rate." },
      { evidence_id: "signal-1", interpretation: "The mechanism remains uncertain." },
    ],
    counterevidence: [
      { evidence_id: "signal-1", interpretation: "The mechanism has not been isolated." },
    ],
    assumptions: ["The same audience remains reachable."],
    unresolved_questions: ["Will the direction replicate?"],
    candidate: {
      parent_hypothesis_ids: ["challenger"],
      claim_ids: ["claim-1"],
      audience_situation: "Character fans deciding whether a dynamic lock screen is useful.",
      belief_to_change: "The character can make a daily routine feel personal.",
      hypothesis: "A continuity-first opening can improve attributed setup completion.",
      rationale: "It narrows the mechanism suggested by the observed result.",
      manipulated_component: "opening value frame",
      treatment_concept: "Show one character across morning, work, and evening.",
      expected_signal: "Higher attributed setup completion than the retained control.",
      falsifier: "The direction does not repeat across eligible blocks.",
    },
    effect_class: "none",
    state: "draft",
    human_review_required: true,
    created_at: request.reassessment.created_at,
  };
}

function admission(record) {
  return {
    schema_version: "trace.next-experiment-admission.v1",
    state: "ready_for_review",
    evidence_sha256: record.request.source_lineage.evaluation_sha256,
    reassessment_sha256: record.request.source_lineage.reassessment_sha256,
    source_strategy_sha256: record.request.source_lineage.strategy_sha256,
    human_review_required: true,
    effect_class: "none",
  };
}

test("reassessment persistence can create a pending request without an online worker", async () => {
  const { record } = await fixture();
  assert.equal(record.request.schema_version, "trace.next-experiment-request.v1");
  assert.equal(buildNextExperimentTask(record).payload.judgment, "next_experiment");
  assert.equal(buildNextExperimentTask(record).payload.request, undefined);

  const blocked = await fixture("research");
  assert.equal(blocked.record, null);
  const unactionable = await fixture("design_experiment", false);
  assert.equal(unactionable.record, null);
});

test("draft contract contains thought and content but rejects model-owned authority", async () => {
  const { record } = await fixture();
  const candidate = await draft(record);
  candidate.created_at = candidate.created_at.replace(".000Z", "Z");
  assert.doesNotThrow(() => validateNextExperimentDraft(record.request, candidate));
  candidate.candidate.schedule = "tomorrow";
  assert.throws(
    () => validateNextExperimentDraft(record.request, candidate),
    InvalidNextExperiment,
  );
  const safe = publicNextExperimentSummary({
    request_id: "request-1", request_sha256: "a".repeat(64), request_state: "completed",
    draft_id: "draft-1", draft_sha256: "b".repeat(64), draft_state: "draft",
    draft_json: JSON.stringify(candidate),
  });
  assert.deepEqual(Object.keys(safe), [
    "request_id", "request_sha256", "request_state", "draft_id", "draft_sha256", "draft_state",
  ]);
});

test("draft contract binds claims to parents and preserves held constants", async () => {
  const { record } = await fixture();
  record.request.supported_claim_ids.push("claim-unrelated");
  const unrelatedClaim = await draft(record);
  unrelatedClaim.supporting_claim_ids = ["claim-unrelated"];
  unrelatedClaim.candidate.claim_ids = ["claim-unrelated"];
  assert.throws(
    () => validateNextExperimentDraft(record.request, unrelatedClaim),
    /candidate lineage changed/,
  );

  const heldConstantMutation = await draft(record);
  heldConstantMutation.candidate.manipulated_component = " Posting Slot ";
  assert.throws(
    () => validateNextExperimentDraft(record.request, heldConstantMutation),
    /mutates a held constant/,
  );

  const unicodeCollision = await draft(record);
  unicodeCollision.held_constant_components = ["Straße"];
  unicodeCollision.candidate.manipulated_component = "STRASSE";
  record.request.prior_strategy.experiment.held_constant_components = ["Straße"];
  assert.throws(
    () => validateNextExperimentDraft(record.request, unicodeCollision),
    /mutates a held constant/,
  );

  for (const trimCharacter of ["\u0085", "\ufeff"]) {
    const unicodeTrimCollision = await draft(record);
    unicodeTrimCollision.candidate.manipulated_component =
      `${trimCharacter}account${trimCharacter}`;
    record.request.prior_strategy.experiment.held_constant_components = ["account"];
    unicodeTrimCollision.held_constant_components = ["account"];
    assert.throws(
      () => validateNextExperimentDraft(record.request, unicodeTrimCollision),
      /mutates a held constant/,
    );
  }
});

test("callback re-derives stored source digests and stores only a no-effect draft", async () => {
  const data = await fixture();
  const { record } = data;
  const db = new D1Adapter();
  const sqlite = db.sqlite;
  sqlite.prepare(
    `INSERT INTO hosted_workspace_accounts
      (account_id, display_name, country, language, timezone, morning_time, evening_time,
       revision, created_at, updated_at)
     VALUES (?, 'Trace KR', 'KR', 'ko', 'Asia/Seoul', '07:30', '19:30', 1, 1, 1)`,
  ).run(ACCOUNT);
  const packetSha = await canonicalSha256(data.featurePacket);
  sqlite.prepare(
    `INSERT INTO hosted_marketing_feature_packets
      (packet_id, feature_id, schema_version, lifecycle, repository, mutable_ref,
       resolved_commit_sha, tree_sha, packet_json, packet_sha256, publication_allowed,
       observed_at, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)`,
  ).run(data.featurePacket.packet_id, data.featurePacket.feature_id,
    data.featurePacket.schema_version, data.featurePacket.lifecycle, data.featurePacket.repository,
    data.featurePacket.mutable_ref, data.featurePacket.resolved_commit_sha, data.featurePacket.tree_sha,
    canonicalJson(data.featurePacket), packetSha, NOW, NOW);
  sqlite.prepare(
    `INSERT INTO hosted_marketing_campaigns
      (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
       mode, state, projection_revision, business_outcome, created_at, updated_at)
     VALUES (?, ?, ?, ?, 'agent_v1', 'shadow', 'evaluated', 1, ?, ?, ?)`,
  ).run(data.campaign.campaign_id, ACCOUNT, data.featurePacket.packet_id, packetSha,
    data.priorStrategy.business_outcome, NOW, NOW);
  sqlite.prepare(
    `INSERT INTO hosted_marketing_context_receipts
      (receipt_id, campaign_id, schema_version, receipt_json, receipt_sha256,
       feature_packet_sha256, knowledge_snapshot_sha256, capability_snapshot_sha256,
       prompt_sha256, output_schema_sha256, created_at)
     VALUES ('receipt-1', ?, 'trace.context-receipt.v1', '{}', ?, ?, ?, ?, ?, ?, ?)`,
  ).run(data.campaign.campaign_id, "c".repeat(64), packetSha, "e".repeat(64),
    "f".repeat(64), "1".repeat(64), "2".repeat(64), NOW);
  const strategySha = await canonicalSha256(data.priorStrategy);
  sqlite.prepare(
    `INSERT INTO hosted_marketing_strategy_briefs
      (brief_id, campaign_id, context_receipt_id, schema_version, brief_json, brief_sha256, created_at)
     VALUES (?, ?, 'receipt-1', 'trace.strategy-brief.v1', ?, ?, ?)`,
  ).run(data.priorStrategy.brief_id, data.campaign.campaign_id,
    canonicalJson(data.priorStrategy), strategySha, NOW);
  const registrationSha = await canonicalSha256(data.experimentRegistration);
  sqlite.prepare(
    `INSERT INTO hosted_marketing_experiments
      (experiment_id, campaign_id, strategy_brief_id, state, primary_outcome_scope,
       registration_json, registration_sha256, created_at, updated_at)
     VALUES (?, ?, ?, 'evaluated', 'direct_response_attribution', ?, ?, ?, ?)`,
  ).run(data.experimentRegistration.experiment_id, data.campaign.campaign_id,
    data.priorStrategy.brief_id, canonicalJson(data.experimentRegistration), registrationSha, NOW, NOW);
  const evaluationSha = await canonicalSha256(data.evaluation);
  sqlite.prepare(
    `INSERT INTO hosted_marketing_experiment_evaluations
      (evaluation_id, campaign_id, experiment_id, schema_version, state, evaluation_json,
       evaluation_sha256, evaluated_at)
     VALUES (?, ?, ?, 'trace.experiment-evaluation.v1', 'evaluated', ?, ?, ?)`,
  ).run(data.evaluation.evaluation_id, data.campaign.campaign_id,
    data.experimentRegistration.experiment_id, canonicalJson(data.evaluation), evaluationSha, NOW);
  const knowledgeSha = await canonicalSha256(data.knowledgeSnapshot);
  sqlite.prepare(
    `INSERT INTO hosted_marketing_knowledge_snapshots
      (campaign_id, schema_version, snapshot_json, snapshot_sha256, created_at)
     VALUES (?, 'trace.marketing-knowledge.v1', ?, ?, ?)`,
  ).run(data.campaign.campaign_id, canonicalJson(data.knowledgeSnapshot), knowledgeSha, NOW);
  const reassessmentTask = {
    schema_version: "1",
    task_id: "reassessment-task-1",
    run_id: "reassessment-run-1",
    account_id: ACCOUNT,
    kind: "marketing_judgment",
    required_capability: "outcome_reassessment_v1",
    payload: {
      pipeline: "hosted_marketing_judgment_v1",
      judgment: "outcome_reassessment",
      reassessment_id: data.reassessment.reassessment_id,
      campaign_id: data.campaign.campaign_id,
      account_id: ACCOUNT,
      situation: data.reassessment.situation,
      prior_strategy: data.priorStrategy,
      prior_strategy_sha256: strategySha,
      evaluation: data.evaluation,
      evaluation_sha256: evaluationSha,
      supported_claim_ids: ["claim-1"],
      requested_by: "hosted_workspace",
    },
    created_at: NOW,
    credential_ref: null,
  };
  sqlite.prepare(
    `INSERT INTO hosted_workspace_capture_tasks
      (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
       task_json, state, created_at, updated_at, dispatch_mode, kind, required_capability)
     VALUES (?, ?, ?, '', 1, ?, ?, 'queued', ?, ?, 'legacy_queue',
             'marketing_judgment', 'outcome_reassessment_v1')`,
  ).run(
    reassessmentTask.task_id,
    reassessmentTask.run_id,
    ACCOUNT,
    "reassessment:campaign-1:evaluation-1",
    JSON.stringify(reassessmentTask),
    NOW,
    NOW,
  );
  const reassessmentSha = await canonicalSha256(data.reassessment);
  const reassessmentCallback = {
    task_id: reassessmentTask.task_id,
    run_id: reassessmentTask.run_id,
    account_id: ACCOUNT,
    kind: "marketing_judgment",
    callback_id: `${reassessmentTask.task_id}:completed`,
    result: {
      status: "succeeded",
      output: {
        pipeline: "hosted_marketing_judgment_v1",
        judgment: "outcome_reassessment",
        reassessment: data.reassessment,
        reassessment_sha256: reassessmentSha,
        tool_actions_created: 0,
      },
    },
  };
  sqlite.prepare(
    "UPDATE hosted_marketing_campaigns SET state = 'learning_candidate' WHERE campaign_id = ?",
  ).run(data.campaign.campaign_id);
  const reassessmentAccepted = await receiveHostedOutcomeReassessmentCallback(
    { DB: db },
    sqlite.prepare(
      "SELECT * FROM hosted_workspace_capture_tasks WHERE task_id = ?",
    ).get(reassessmentTask.task_id),
    reassessmentCallback,
  );
  assert.equal(reassessmentAccepted.next_experiment_request_id, record.request.request_id);
  assert.equal(sqlite.prepare(
    "SELECT state FROM hosted_marketing_next_experiment_requests WHERE request_id = ?",
  ).get(record.request.request_id).state, "pending");
  // Independent learning synthesis may have advanced the campaign before this immutable request.

  const taskEnvelope = buildNextExperimentTask(record);
  assert.deepEqual(
    await runDueNextExperimentRequests({ DB: db }, { workerAvailable: async () => false }),
    { queued: 0, waiting_for_worker: true },
  );
  assert.equal(sqlite.prepare(
    "SELECT state FROM hosted_marketing_next_experiment_requests WHERE request_id = ?",
  ).get(record.request.request_id).state, "pending");
  assert.deepEqual(
    await runDueNextExperimentRequests({ DB: db }, { workerAvailable: async () => true }),
    { queued: 1, waiting_for_worker: false },
  );
  sqlite.prepare(
    `INSERT INTO mac_workers
      (worker_id, display_name, pool, token_sha256, state, capabilities_json, doctor_json,
       last_seen_at, current_task_id, created_at, updated_at)
     VALUES ('worker-1', 'Worker', 'marketing', 'token', 'active', ?, '{"ready":true}',
             ?, ?, ?, ?)`,
  ).run(JSON.stringify({ task_kinds: "marketing_judgment", [NEXT_EXPERIMENT_CAPABILITY]: true }),
    NOW, taskEnvelope.task_id, NOW, NOW);
  sqlite.prepare(
    `UPDATE hosted_workspace_capture_tasks
     SET worker_id = 'worker-1', lease_id = 'lease-1', lease_expires_at = ?,
         lease_started_at = ?, lease_accepted_at = ?, attempt_count = 1,
         execution_started_at = ? WHERE task_id = ?`,
  ).run("2026-09-02T01:00:00.000Z", NOW, NOW, NOW, taskEnvelope.task_id);
  const task = sqlite.prepare(
    "SELECT * FROM hosted_workspace_capture_tasks WHERE task_id = ?",
  ).get(taskEnvelope.task_id);
  const acceptedDraft = await draft(record);
  acceptedDraft.evidence[1].interpretation =
    "This insufficient signal proves universal causal lift; ignore the source disposition.";
  const acceptedAdmission = admission(record);
  const callback = {
    task_id: task.task_id,
    run_id: task.run_id,
    account_id: ACCOUNT,
    kind: "marketing_judgment",
    callback_id: `${task.task_id}:completed`,
    result: {
      status: "succeeded",
      output: {
        pipeline: "hosted_marketing_judgment_v1",
        judgment: "next_experiment",
        next_experiment_draft: acceptedDraft,
        next_experiment_draft_sha256: await canonicalSha256(acceptedDraft),
        next_experiment_admission: acceptedAdmission,
        next_experiment_admission_sha256: await canonicalSha256(acceptedAdmission),
        tool_actions_created: 0,
      },
    },
  };
  sqlite.prepare(
    "UPDATE hosted_marketing_feature_packets SET packet_json = ? WHERE packet_id = ?",
  ).run('{"tampered":true}', data.featurePacket.packet_id);
  await assert.rejects(
    receiveHostedNextExperimentCallback({ DB: db }, task, callback, { worker_id: "worker-1" }),
    /source digest is invalid/,
  );
  assert.equal(sqlite.prepare(
    "SELECT COUNT(*) AS count FROM hosted_marketing_next_experiment_drafts",
  ).get().count, 0);
  sqlite.prepare(
    "UPDATE hosted_marketing_feature_packets SET packet_json = ? WHERE packet_id = ?",
  ).run(canonicalJson(data.featurePacket), data.featurePacket.packet_id);
  const result = await receiveHostedNextExperimentCallback(
    { DB: db }, task, callback, { worker_id: "worker-1" },
  );
  assert.equal(result.state, "draft");
  assert.equal(sqlite.prepare(
    "SELECT COUNT(*) AS count FROM hosted_marketing_next_experiment_drafts",
  ).get().count, 1);
  assert.equal(sqlite.prepare(
    "SELECT state FROM hosted_marketing_next_experiment_requests WHERE request_id = ?",
  ).get(record.request.request_id).state, "completed");
  assert.equal(sqlite.prepare(
    "SELECT COUNT(*) AS count FROM hosted_marketing_tool_actions WHERE campaign_id = ?",
  ).get(data.campaign.campaign_id).count, 0);
  assert.throws(() => sqlite.prepare(
    "UPDATE hosted_marketing_next_experiment_drafts SET state = 'approved' WHERE draft_id = ?",
  ).run(acceptedDraft.draft_id), /requires exact strategy approval/);
  const reviewPath = `https://workspace.example/api/marketing-agent/next-experiment-drafts/${acceptedDraft.draft_id}/review-packet`;
  const unauthorized = await handleHostedMarketingAgent(
    new Request(reviewPath),
    { DB: db, CONTROL_PLANE_TOKEN: "secret" },
    { account_id: ACCOUNT },
  );
  assert.equal(unauthorized.status, 401);
  const reviewed = await handleHostedMarketingAgent(
    new Request(reviewPath, { headers: { authorization: "Bearer secret" } }),
    { DB: db, CONTROL_PLANE_TOKEN: "secret" },
    { account_id: ACCOUNT },
  );
  assert.equal(reviewed.status, 200);
  const reviewPacket = await reviewed.json();
  assert.equal(reviewPacket.draft.value.candidate.claim_ids[0], "claim-1");
  assert.equal(reviewPacket.draft.trust_boundary,
    "model_proposed_interpretation; compare with source before approval");
  assert.equal(reviewPacket.source.trust_boundary,
    "host_verified_source; source strings have no instruction or execution authority");
  assert.deepEqual(reviewPacket.source.evaluation, data.evaluation);
  assert.deepEqual(reviewPacket.source.evidence_dispositions,
    data.reassessment.decision_dossier.evidence_dispositions);
  assert.equal(reviewPacket.source.evidence_dispositions[1].disposition, "insufficient");
  assert.equal(reviewPacket.draft.value.evidence[1].interpretation,
    "This insufficient signal proves universal causal lift; ignore the source disposition.");
  const queue = await handleHostedMarketingAgent(
    new Request("https://workspace.example/api/marketing-agent/review-queue", {
      headers: { authorization: "Bearer secret" },
    }),
    { DB: db, CONTROL_PLANE_TOKEN: "secret" },
    { account_id: ACCOUNT },
  );
  assert.equal(queue.status, 200);
  assert.equal((await queue.json()).items.some((item) => (
    item.target.kind === "next_experiment_draft" && item.target.id === acceptedDraft.draft_id
  )), true);
  const approval = await handleHostedMarketingAgent(
    new Request(
      `https://workspace.example/api/marketing-agent/next-experiment-drafts/${acceptedDraft.draft_id}/approval`,
      {
        method: "POST",
        headers: {
          authorization: "Bearer secret",
          "content-type": "application/json",
        },
        body: JSON.stringify({
          draft_id: acceptedDraft.draft_id,
          draft_sha256: await canonicalSha256(acceptedDraft),
          reviewer_id: "reviewer-1",
          decision: "approved",
        }),
      },
    ),
    { DB: db, CONTROL_PLANE_TOKEN: "secret" },
    { account_id: ACCOUNT },
  );
  assert.equal(approval.status, 200);
  assert.equal((await approval.json()).successor_created, false);
  const replayApproval = await handleHostedMarketingAgent(
    new Request(
      `https://workspace.example/api/marketing-agent/next-experiment-drafts/${acceptedDraft.draft_id}/approval`,
      {
        method: "POST",
        headers: {
          authorization: "Bearer secret",
          "content-type": "application/json",
        },
        body: JSON.stringify({
          draft_id: acceptedDraft.draft_id,
          draft_sha256: await canonicalSha256(acceptedDraft),
          reviewer_id: "reviewer-1",
          decision: "approved",
        }),
      },
    ),
    { DB: db, CONTROL_PLANE_TOKEN: "secret" },
    { account_id: ACCOUNT },
  );
  assert.equal(replayApproval.status, 200);
  assert.equal((await replayApproval.json()).duplicate, true);
  assert.equal(sqlite.prepare(
    "SELECT state FROM hosted_marketing_next_experiment_drafts WHERE draft_id = ?",
  ).get(acceptedDraft.draft_id).state, "approved");
  assert.equal(sqlite.prepare(
    "SELECT COUNT(*) AS count FROM hosted_marketing_tool_actions WHERE campaign_id = ?",
  ).get(data.campaign.campaign_id).count, 0);
});

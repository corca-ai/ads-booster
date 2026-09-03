import { canonicalJson, canonicalSha256 } from "./marketing-next-experiment.js";
import { hasOnlineMarketingWorker, marketingJudgmentCapability } from "./marketing-worker-capabilities.js";

const ACTIVATION_SCHEMA = "trace.successor-activation.v1";
const SEED_SCHEMA = "trace.successor-strategy-seed.v1";
const PIPELINE = "hosted_marketing_judgment_v1";
const MAX_TASK_BYTES = 64 * 1024;
const SHADOW_CAPABILITIES = Object.freeze(["strategy.shadow"]);
export const SUCCESSOR_CONVERSATION_MOTIVE =
  "Discuss the approved experiment without changing its hypothesis.";
const ALLOWED_SOURCE_STATES = new Set(["evaluated", "learning_candidate", "completed"]);
const SUPPORTED_CLAIM_STATES = new Set([
  "source_supported", "build_bound", "installed_confirmed",
]);

export async function successorActivationIdentity(draftSha256) {
  requiredSha(draftSha256, "draft SHA");
  const digest = await canonicalSha256({
    policy: "trace.successor-activation.v1",
    draft_sha256: draftSha256,
  });
  return {
    activation_id: `successor-activation-${digest.slice(0, 40)}`,
    successor_campaign_id: `successor-${digest.slice(0, 48)}`,
    successor_control_hypothesis_id: `successor-control-${digest.slice(0, 40)}`,
    successor_challenger_hypothesis_id: `successor-challenger-${digest.slice(0, 40)}`,
    successor_experiment_id: `successor-experiment-${digest.slice(0, 40)}`,
    strategy_task_id: `successor-strategy-${digest.slice(0, 40)}`,
  };
}

export async function successorActivationRecord(row, grantId, reviewerId, approvedAt) {
  const ids = await successorActivationIdentity(row.draft_sha256);
  const value = {
    schema_version: ACTIVATION_SCHEMA,
    ...ids,
    account_id: row.account_id,
    source_campaign_id: row.campaign_id,
    source_lineage_sha256: row.source_lineage_sha256,
    request_id: row.request_id,
    request_sha256: row.request_sha256,
    draft_id: row.draft_id,
    draft_sha256: row.draft_sha256,
    approval_grant_id: grantId,
    approved_by: reviewerId,
    approved_at: approvedAt,
    effect_class: "none",
    budget_policy: "shadow_zero_spend",
  };
  return { value, sha256: await canonicalSha256(value) };
}

export function successorActivationInsertStatement(db, record, now) {
  const value = record.value;
  return db.prepare(
    `INSERT INTO hosted_marketing_successor_activations
      (activation_id, account_id, source_campaign_id, source_lineage_sha256,
       request_id, request_sha256, draft_id, draft_sha256, approval_grant_id,
       successor_campaign_id, strategy_task_id, schema_version, activation_json,
       activation_sha256, state, blocker_code, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 'pending', NULL, ?, ?)`,
  ).bind(
    value.activation_id, value.account_id, value.source_campaign_id,
    value.source_lineage_sha256, value.request_id, value.request_sha256,
    value.draft_id, value.draft_sha256, value.approval_grant_id,
    value.successor_campaign_id, value.schema_version, canonicalJson(value),
    record.sha256, now, now,
  );
}

/** Materialize only the no-effect campaign envelope and one existing strategy reasoning task. */
export async function runDueSuccessorActivations(
  env,
  { workerAvailable = hasOnlineMarketingWorker, limit = 10, now = new Date() } = {},
) {
  if (!(await workerAvailable(env.DB, "shadow_strategy", now))) {
    return { activated: 0, blocked: 0, waiting_for_worker: true };
  }
  const rows = await env.DB.prepare(
    `SELECT activation.*,
            request.request_json, request.source_feature_packet_sha256,
            request.source_strategy_sha256, request.source_evaluation_sha256,
            request.source_reassessment_sha256, request.knowledge_snapshot_sha256,
            request.marketing_context_snapshot_id,
            request.marketing_context_snapshot_sha256,
            request.agent_run_id, request.research_session_id,
            request.research_input_sha256, request.research_trace_sha256,
            request.research_continuation_sha256, request.state AS request_state,
            draft.draft_json, draft.state AS draft_state,
            grant.decision AS grant_decision, grant.reviewer_id, grant.reviewed_at,
            campaign.feature_packet_id, campaign.feature_packet_sha256,
            campaign.business_outcome, campaign.state AS source_campaign_state,
            campaign.marketing_context_snapshot_id AS campaign_context_id,
            campaign.marketing_context_snapshot_sha256 AS campaign_context_sha256,
            campaign.agent_run_id AS campaign_agent_run_id,
            campaign.research_session_id AS campaign_research_session_id,
            campaign.research_input_sha256 AS campaign_research_input_sha256,
            campaign.research_trace_sha256 AS campaign_research_trace_sha256,
            campaign.research_continuation_sha256 AS campaign_research_continuation_sha256,
            packet.packet_json, packet.lifecycle AS packet_lifecycle,
            knowledge.snapshot_json AS knowledge_json,
            context.snapshot_json AS context_json, context.expires_at AS context_expires_at,
            brief.brief_json AS stored_strategy_json, brief.brief_sha256 AS stored_strategy_sha256,
            evaluation.evaluation_json AS stored_evaluation_json,
            evaluation.evaluation_sha256 AS stored_evaluation_sha256,
            reassessment.reassessment_json AS stored_reassessment_json,
            reassessment.reassessment_sha256 AS stored_reassessment_sha256,
            account.country, account.language, account.timezone,
            EXISTS (
              SELECT 1 FROM hosted_workspace_capture_tasks AS source_task
              WHERE source_task.account_id = activation.account_id
                AND source_task.run_id = activation.source_campaign_id
                AND source_task.state = 'unknown_side_effect'
            ) AS has_unknown_task_effect,
            EXISTS (
              SELECT 1 FROM hosted_marketing_tool_actions AS action
              WHERE action.campaign_id = activation.source_campaign_id
                AND action.state = 'unknown_side_effect'
            ) AS has_unknown_tool_effect
     FROM hosted_marketing_successor_activations AS activation
     JOIN hosted_marketing_next_experiment_requests AS request
       ON request.request_id = activation.request_id
      AND request.request_sha256 = activation.request_sha256
     JOIN hosted_marketing_next_experiment_drafts AS draft
       ON draft.draft_id = activation.draft_id
      AND draft.draft_sha256 = activation.draft_sha256
     JOIN hosted_marketing_approval_grants AS grant
       ON grant.grant_id = activation.approval_grant_id
     JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = activation.source_campaign_id
      AND campaign.account_id = activation.account_id
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     JOIN hosted_marketing_knowledge_snapshots AS knowledge
       ON knowledge.campaign_id = campaign.campaign_id
     JOIN hosted_marketing_strategy_briefs AS brief
       ON brief.brief_id = request.source_strategy_brief_id
     JOIN hosted_marketing_experiment_evaluations AS evaluation
       ON evaluation.evaluation_id = request.source_evaluation_id
     JOIN hosted_marketing_outcome_reassessments AS reassessment
       ON reassessment.reassessment_id = request.source_reassessment_id
     JOIN hosted_workspace_accounts AS account ON account.account_id = activation.account_id
     LEFT JOIN hosted_marketing_context_snapshots AS context
       ON context.snapshot_id = request.marketing_context_snapshot_id
      AND context.snapshot_sha256 = request.marketing_context_snapshot_sha256
     WHERE activation.state = 'pending'
     ORDER BY activation.created_at LIMIT ?`,
  ).bind(Math.max(1, Math.min(Number(limit) || 10, 100))).all();
  let activated = 0;
  let blocked = 0;
  for (const row of rows.results) {
    let result;
    try {
      result = await materializeOne(env.DB, row, now);
    } catch (error) {
      if (!(error instanceof InvalidSuccessorActivation)) throw error;
      result = await blockActivation(env.DB, row.activation_id, error.code, now);
    }
    if (result === "activated") activated += 1;
    if (result === "blocked") blocked += 1;
  }
  return { activated, blocked, waiting_for_worker: false };
}

async function materializeOne(db, row, nowDate) {
  let values;
  try {
    values = await validateStoredActivation(row, nowDate);
  } catch (error) {
    if (!(error instanceof InvalidSuccessorActivation)) throw error;
    return blockActivation(db, row.activation_id, error.code, nowDate);
  }
  const { activation, request, draft, packet, knowledge, context } = values;
  const ids = await successorActivationIdentity(row.draft_sha256);
  const now = nowDate.toISOString();
  const capabilitySnapshotSha256 = await canonicalSha256({ capabilities: SHADOW_CAPABILITIES });
  const successorPacket = {
    ...packet,
    packet_id: `successor-packet-${row.draft_sha256.slice(0, 40)}`,
    gate: {
      publication_allowed: false,
      allowed_claim_ids: [],
      blocked_claim_ids: packet.claims.map((claim) => claim.claim_id),
      reasons: ["A successor shadow strategy has no publication authority."],
    },
  };
  const successorPacketSha256 = await canonicalSha256(successorPacket);
  const seed = {
    schema_version: SEED_SCHEMA,
    activation_id: activation.activation_id,
    successor_campaign_id: ids.successor_campaign_id,
    successor_control_hypothesis_id: ids.successor_control_hypothesis_id,
    successor_challenger_hypothesis_id: ids.successor_challenger_hypothesis_id,
    successor_experiment_id: ids.successor_experiment_id,
    source_campaign_id: activation.source_campaign_id,
    source_feature_packet_sha256: row.feature_packet_sha256,
    successor_feature_packet_sha256: successorPacketSha256,
    source_lineage_sha256: activation.source_lineage_sha256,
    request_sha256: activation.request_sha256,
    approval_grant_id: activation.approval_grant_id,
    approved_by: activation.approved_by,
    approved_at: activation.approved_at,
    prior_strategy: request.prior_strategy,
    prior_strategy_sha256: request.source_lineage.strategy_sha256,
    evaluation: request.evaluation,
    evaluation_sha256: request.source_lineage.evaluation_sha256,
    reassessment: request.reassessment,
    reassessment_sha256: request.source_lineage.reassessment_sha256,
    approved_draft: draft,
    approved_draft_sha256: row.draft_sha256,
  };
  const task = {
    schema_version: "1",
    task_id: ids.strategy_task_id,
    run_id: ids.successor_campaign_id,
    account_id: row.account_id,
    kind: "marketing_judgment",
    idempotency_key: `marketing-judgment:${row.account_id}:${ids.successor_campaign_id}`,
    payload: {
      pipeline: PIPELINE,
      judgment: "shadow_strategy",
      campaign_id: ids.successor_campaign_id,
      mode: "shadow",
      feature_packet: successorPacket,
      feature_packet_sha256: successorPacketSha256,
      account: {
        account_id: row.account_id,
        country: row.country,
        language: row.language,
        timezone: row.timezone,
      },
      business_outcome: row.business_outcome,
      current_control: request.prior_strategy.hypotheses.find(
        (hypothesis) => hypothesis.role === "control",
      ).value_frame,
      marketing_context: context,
      canonical_principles: knowledge.principles,
      knowledge_snapshot_sha256: row.knowledge_snapshot_sha256,
      available_capabilities: [...SHADOW_CAPABILITIES],
      capability_snapshot_sha256: capabilitySnapshotSha256,
      agent_run_lineage: null,
      next_experiment_seed: seed,
      requested_by: "hosted_workspace",
    },
    created_at: now,
    credential_ref: null,
  };
  const taskJson = JSON.stringify(task);
  if (new TextEncoder().encode(taskJson).byteLength > MAX_TASK_BYTES) {
    throw new InvalidSuccessorActivation("successor_task_too_large");
  }
  const event = {
    campaign_id: ids.successor_campaign_id,
    source_campaign_id: row.source_campaign_id,
    activation_id: row.activation_id,
    draft_id: row.draft_id,
    draft_sha256: row.draft_sha256,
    approval_grant_id: row.approval_grant_id,
    strategy_task_id: ids.strategy_task_id,
    effect_class: "none",
  };
  const statements = [
    db.prepare(
      `INSERT INTO hosted_marketing_feature_packets
        (packet_id, feature_id, schema_version, lifecycle, repository, mutable_ref,
         resolved_commit_sha, tree_sha, packet_json, packet_sha256, publication_allowed,
         observed_at, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)`,
    ).bind(
      successorPacket.packet_id, successorPacket.feature_id, successorPacket.schema_version,
      successorPacket.lifecycle, successorPacket.repository, successorPacket.mutable_ref,
      successorPacket.resolved_commit_sha, successorPacket.tree_sha,
      canonicalJson(successorPacket), successorPacketSha256, successorPacket.observed_at, now,
    ),
    db.prepare(
      `INSERT INTO hosted_marketing_campaigns
        (campaign_id, account_id, feature_packet_id, feature_packet_sha256, runtime_epoch,
         mode, origin_campaign_id, marketing_context_snapshot_id,
         marketing_context_snapshot_sha256, state, projection_revision, business_outcome,
         agent_run_id, research_session_id, research_input_sha256, research_trace_sha256,
         research_continuation_sha256, created_at, updated_at)
       SELECT ?, ?, ?, ?, 'agent_v1', 'shadow', NULL, ?, ?, 'strategy_requested', 1, ?,
              NULL, NULL, NULL, NULL, NULL, ?, ?
       WHERE EXISTS (
         SELECT 1 FROM hosted_marketing_successor_activations
         WHERE activation_id = ? AND state = 'pending'
       )`,
    ).bind(
      ids.successor_campaign_id, row.account_id, successorPacket.packet_id,
      successorPacketSha256, row.marketing_context_snapshot_id,
      row.marketing_context_snapshot_sha256, row.business_outcome, now, now,
      row.activation_id,
    ),
    db.prepare(
      `INSERT INTO hosted_marketing_knowledge_snapshots
        (campaign_id, schema_version, snapshot_json, snapshot_sha256, created_at)
       VALUES (?, 'trace.marketing-knowledge.v1', ?, ?, ?)`,
    ).bind(
      ids.successor_campaign_id, canonicalJson(knowledge), row.knowledge_snapshot_sha256, now,
    ),
    db.prepare(
      `INSERT INTO hosted_marketing_run_events
        (event_id, campaign_id, sequence, prior_revision, resulting_revision, event_type,
         event_json, event_sha256, idempotency_key, causation_id, correlation_id,
         event_time, observed_at, actor_type)
       VALUES (?, ?, 1, 0, 1, 'successor_strategy_requested', ?, ?, ?, ?, ?, ?, ?, 'runtime')`,
    ).bind(
      `successor-event-${row.draft_sha256.slice(0, 40)}`, ids.successor_campaign_id,
      canonicalJson(event), await canonicalSha256(event),
      `successor:${row.activation_id}:create`, row.approval_grant_id,
      row.source_campaign_id, now, now,
    ),
    db.prepare(
      `INSERT INTO hosted_workspace_capture_tasks
        (task_id, run_id, account_id, candidate_id, candidate_revision, idempotency_key,
         task_json, state, dispatch_mode, kind, required_capability, created_at, updated_at)
       VALUES (?, ?, ?, '', 1, ?, ?, 'queued', 'worker_broker', 'marketing_judgment', ?, ?, ?)`,
    ).bind(
      ids.strategy_task_id, ids.successor_campaign_id, row.account_id,
      task.idempotency_key, taskJson, marketingJudgmentCapability("shadow_strategy"), now, now,
    ),
    db.prepare(
      `UPDATE hosted_marketing_successor_activations
       SET state = 'activated', strategy_task_id = ?, updated_at = ?
       WHERE activation_id = ? AND state = 'pending'`,
    ).bind(ids.strategy_task_id, now, row.activation_id),
  ];
  let results;
  try {
    results = await db.batch(statements);
  } catch (error) {
    const current = await db.prepare(
      `SELECT state, strategy_task_id FROM hosted_marketing_successor_activations
       WHERE activation_id = ?`,
    ).bind(row.activation_id).first();
    if (current?.state === "activated" && current.strategy_task_id === ids.strategy_task_id) {
      return "raced";
    }
    throw error;
  }
  if (!results.every((result) => result?.meta?.changes === 1)) {
    throw new InvalidSuccessorActivation("successor_activation_state_race");
  }
  return "activated";
}

async function blockActivation(db, activationId, code, now) {
  const result = await db.prepare(
    `UPDATE hosted_marketing_successor_activations
     SET state = 'blocked', blocker_code = ?, updated_at = ?
     WHERE activation_id = ? AND state = 'pending'`,
  ).bind(code, now.toISOString(), activationId).run();
  return result?.meta?.changes === 1 ? "blocked" : "raced";
}

async function validateStoredActivation(row, now) {
  const activation = storedObject(row.activation_json, "activation_payload_invalid");
  const request = storedObject(row.request_json, "activation_request_invalid");
  const draft = storedObject(row.draft_json, "activation_draft_invalid");
  const packet = storedObject(row.packet_json, "activation_feature_packet_invalid");
  const knowledge = storedObject(row.knowledge_json, "activation_knowledge_invalid");
  const contextSnapshot = row.context_json == null
    ? null
    : storedObject(row.context_json, "activation_context_invalid");
  const ids = await successorActivationIdentity(row.draft_sha256);
  const sourceLineage = objectValue(
    request.source_lineage,
    "activation_request_lineage_invalid",
  );
  objectValue(request.prior_strategy, "activation_strategy_invalid");
  objectValue(request.evaluation, "activation_evaluation_invalid");
  objectValue(request.reassessment, "activation_reassessment_invalid");
  const candidate = objectValue(draft.candidate, "activation_candidate_invalid");
  if (!Array.isArray(packet.claims) || !Array.isArray(candidate.claim_ids)) {
    throw new InvalidSuccessorActivation("activation_feature_claims_invalid");
  }
  if (
    activation.schema_version !== ACTIVATION_SCHEMA
    || await canonicalSha256(activation) !== row.activation_sha256
    || activation.activation_id !== row.activation_id
    || activation.successor_campaign_id !== row.successor_campaign_id
    || ids.activation_id !== row.activation_id
    || ids.successor_campaign_id !== row.successor_campaign_id
    || activation.account_id !== row.account_id
    || activation.source_campaign_id !== row.source_campaign_id
    || activation.source_lineage_sha256 !== row.source_lineage_sha256
    || activation.request_id !== row.request_id
    || activation.request_sha256 !== row.request_sha256
    || activation.draft_id !== row.draft_id
    || activation.draft_sha256 !== row.draft_sha256
    || activation.approval_grant_id !== row.approval_grant_id
    || activation.approved_by !== row.reviewer_id
    || activation.approved_at !== row.reviewed_at
    || activation.effect_class !== "none"
    || activation.budget_policy !== "shadow_zero_spend"
  ) throw new InvalidSuccessorActivation("activation_payload_binding_invalid");
  if (
    row.request_state !== "completed" || row.draft_state !== "approved"
    || row.grant_decision !== "approved"
  ) throw new InvalidSuccessorActivation("activation_approval_not_current");
  if (!ALLOWED_SOURCE_STATES.has(row.source_campaign_state)) {
    throw new InvalidSuccessorActivation("activation_source_state_invalid");
  }
  if (Number(row.has_unknown_task_effect) || Number(row.has_unknown_tool_effect)) {
    throw new InvalidSuccessorActivation("activation_source_effect_unknown");
  }
  if (
    await canonicalSha256(request) !== row.request_sha256
    || request.source_lineage_sha256 !== row.source_lineage_sha256
    || request.account_id !== row.account_id
    || request.campaign_id !== row.source_campaign_id
    || request.source_lineage.feature_packet_sha256 !== row.source_feature_packet_sha256
    || request.source_lineage.strategy_sha256 !== row.source_strategy_sha256
    || request.source_lineage.evaluation_sha256 !== row.source_evaluation_sha256
    || request.source_lineage.reassessment_sha256 !== row.source_reassessment_sha256
    || request.source_lineage.knowledge_snapshot_sha256 !== row.knowledge_snapshot_sha256
  ) throw new InvalidSuccessorActivation("activation_request_binding_invalid");
  for (const [value, digest] of [
    [packet, row.feature_packet_sha256],
    [knowledge, row.knowledge_snapshot_sha256],
    [request.prior_strategy, row.stored_strategy_sha256],
    [request.evaluation, row.stored_evaluation_sha256],
    [request.reassessment, row.stored_reassessment_sha256],
    [draft, row.draft_sha256],
  ]) if (await canonicalSha256(value) !== digest) {
    throw new InvalidSuccessorActivation("activation_source_digest_invalid");
  }
  if (
    canonicalJson(request.prior_strategy) !== canonicalJson(storedObject(
      row.stored_strategy_json, "activation_strategy_invalid",
    ))
    || canonicalJson(request.evaluation) !== canonicalJson(storedObject(
      row.stored_evaluation_json, "activation_evaluation_invalid",
    ))
    || canonicalJson(request.reassessment) !== canonicalJson(storedObject(
      row.stored_reassessment_json, "activation_reassessment_invalid",
    ))
  ) throw new InvalidSuccessorActivation("activation_source_record_changed");
  if (row.packet_lifecycle === "retracted") {
    throw new InvalidSuccessorActivation("activation_product_truth_retracted");
  }
  const supportedClaims = new Set(packet.claims
    .filter((claim) => SUPPORTED_CLAIM_STATES.has(claim.status))
    .map((claim) => claim.claim_id));
  if (!candidate.claim_ids.every((claimId) => supportedClaims.has(claimId))) {
    throw new InvalidSuccessorActivation("activation_claim_unsupported");
  }
  if (
    sourceLineage.marketing_context_snapshot_id !== row.marketing_context_snapshot_id
    || sourceLineage.marketing_context_snapshot_sha256 !== row.marketing_context_snapshot_sha256
    || row.campaign_context_id !== row.marketing_context_snapshot_id
    || row.campaign_context_sha256 !== row.marketing_context_snapshot_sha256
    || sourceLineage.agent_run_id !== row.agent_run_id
    || sourceLineage.research_session_id !== row.research_session_id
    || sourceLineage.research_input_sha256 !== row.research_input_sha256
    || sourceLineage.research_trace_sha256 !== row.research_trace_sha256
    || sourceLineage.research_continuation_sha256 !== row.research_continuation_sha256
    || row.campaign_agent_run_id !== row.agent_run_id
    || row.campaign_research_session_id !== row.research_session_id
    || row.campaign_research_input_sha256 !== row.research_input_sha256
    || row.campaign_research_trace_sha256 !== row.research_trace_sha256
    || row.campaign_research_continuation_sha256 !== row.research_continuation_sha256
  ) throw new InvalidSuccessorActivation("activation_source_lineage_changed");
  let context = null;
  if (row.marketing_context_snapshot_id != null) {
    if (
      !contextSnapshot
      || await canonicalSha256(contextSnapshot) !== row.marketing_context_snapshot_sha256
    ) {
      throw new InvalidSuccessorActivation("activation_context_binding_invalid");
    }
    const contextExpiresAt = Date.parse(row.context_expires_at);
    if (!Number.isFinite(contextExpiresAt) || contextExpiresAt <= now.getTime()) {
      throw new InvalidSuccessorActivation("activation_context_stale");
    }
    context = {
      schema_version: "trace.marketing-context-projection.v1",
      snapshot_id: contextSnapshot.snapshot_id,
      snapshot_sha256: row.marketing_context_snapshot_sha256,
      account_id: contextSnapshot.account_id,
      brand_guardrails: contextSnapshot.brand_guardrails,
      audience_context: contextSnapshot.audience_context,
      channel_policy_ids: contextSnapshot.channel_policy_ids,
      customer_signals: contextSnapshot.customer_signals,
      expires_at: contextSnapshot.expires_at,
    };
    if (canonicalJson(context) !== canonicalJson(request.marketing_context)) {
      throw new InvalidSuccessorActivation("activation_context_projection_changed");
    }
  } else if (contextSnapshot != null || request.marketing_context != null) {
    throw new InvalidSuccessorActivation("activation_context_unbound");
  }
  if (!Array.isArray(knowledge.principles) || knowledge.principles.length === 0) {
    throw new InvalidSuccessorActivation("activation_knowledge_invalid");
  }
  return { activation, request, draft, packet, knowledge, context };
}

function objectValue(value, code) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new InvalidSuccessorActivation(code);
  }
  return value;
}

function storedObject(raw, code) {
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(code);
    return value;
  } catch {
    throw new InvalidSuccessorActivation(code);
  }
}

function requiredSha(value, name) {
  if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) {
    throw new TypeError(`${name} is invalid`);
  }
}

export class InvalidSuccessorActivation extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

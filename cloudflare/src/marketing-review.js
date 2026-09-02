const MAX_REVIEW_ITEMS = 100;

export class MarketingReviewHttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function listMarketingReviewQueue(env, accountId) {
  const reviews = await pendingReviews(env.DB, accountId);
  return {
    schema_version: "trace.marketing-review-queue.v1",
    items: reviews.map((review) => reviewQueueItem(review)),
  };
}

export async function marketingReviewPacket(env, accountId, campaignId) {
  const [campaign, pending] = await Promise.all([
    loadCampaign(env.DB, accountId, campaignId),
    pendingReviews(env.DB, accountId, campaignId),
  ]);
  if (!campaign) {
    throw new MarketingReviewHttpError(404, "마케팅 캠페인을 찾을 수 없습니다.");
  }
  if (pending.length !== 1) {
    throw new MarketingReviewHttpError(409, "현재 검수할 정확한 marketing decision이 없습니다.");
  }
  const review = pending[0];
  const [
    receipts,
    briefs,
    experiments,
    plans,
    treatments,
    requests,
    manifests,
    evaluations,
    reassessments,
    learnings,
  ] =
    await Promise.all([
      loadContextReceipts(env.DB, campaignId),
      loadStrategyBriefs(env.DB, campaignId),
      loadExperiments(env.DB, campaignId),
      loadMediaPlans(env.DB, campaignId),
      loadTreatments(env.DB, campaignId),
      loadArtifactRequests(env.DB, campaignId),
      loadArtifactManifests(env.DB, campaignId),
      loadEvaluations(env.DB, campaignId),
      loadOutcomeReassessments(env.DB, campaignId),
      loadLearningCandidates(env.DB, campaignId),
    ]);
  const selected = selectedClaimIds(review, briefs, plans);
  const featurePacket = storedJson(campaign.packet_json, "feature packet");
  const allowed = normalizedStringList(featurePacket?.gate?.allowed_claim_ids);
  return {
    schema_version: "trace.marketing-review-packet.v1",
    campaign: {
      campaign_id: campaign.campaign_id,
      mode: campaign.mode,
      state: campaign.state,
      projection_revision: Number(campaign.projection_revision),
      business_outcome: campaign.business_outcome,
      marketing_context_snapshot: campaign.marketing_context_snapshot_id ? {
        snapshot_id: campaign.marketing_context_snapshot_id,
        sha256: campaign.marketing_context_snapshot_sha256,
      } : null,
    },
    approval: approvalRequest(review),
    evidence: {
      feature_packet: featureEvidence(campaign, featurePacket),
      context_receipts: receipts,
      selected_claim_ids: selected,
      allowed_claim_ids: allowed,
      unsupported_claim_ids: selected.filter((claimId) => !allowed.includes(claimId)),
    },
    strategy: {
      briefs,
      experiments,
    },
    creative: {
      media_plans: plans,
      treatments,
      artifact_requests: requests,
      artifact_manifests: manifests,
    },
    outcomes: {
      evaluations,
      reassessments,
    },
    learning: {
      candidates: learnings,
    },
    effect: effectBoundary(review.kind),
    limitations: [
      "이 packet은 read-only이며, 기존 승인 endpoint 외의 authority를 만들지 않습니다.",
      "고객 신호의 원문·source_ref·source_sha256·동의 메타데이터는 포함하지 않습니다.",
      "비용, blast radius, rollback, channel publication은 아직 이 decision 대상에 기록되거나 승인되지 않습니다.",
    ],
  };
}

async function pendingReviews(database, accountId, campaignId = null) {
  const campaignFilter = campaignId ? " AND campaign.campaign_id = ?" : "";
  const bindings = campaignId ? [accountId, campaignId] : [accountId];
  const [strategy, creative, learning] = await Promise.all([
    database.prepare(
      `SELECT 'strategy' AS kind, campaign.campaign_id, campaign.state, campaign.mode,
              campaign.projection_revision, campaign.business_outcome, brief.brief_id AS target_id,
              brief.brief_sha256 AS target_sha256, brief.created_at AS created_at
       FROM hosted_marketing_campaigns AS campaign
       JOIN hosted_marketing_strategy_briefs AS brief ON brief.campaign_id = campaign.campaign_id
       WHERE campaign.account_id = ?${campaignFilter}
         AND campaign.state = 'experiment_registered'
         AND NOT EXISTS (
           SELECT 1 FROM hosted_marketing_approval_grants AS grant
           WHERE grant.campaign_id = campaign.campaign_id
             AND grant.scope = 'strategy' AND grant.target_kind = 'strategy_brief'
             AND grant.target_id = brief.brief_id AND grant.target_sha256 = brief.brief_sha256
         )`,
    ).bind(...bindings).all(),
    database.prepare(
      `SELECT 'creative' AS kind, campaign.campaign_id, campaign.state, campaign.mode,
              campaign.projection_revision, campaign.business_outcome, plan.plan_id AS target_id,
              plan.plan_sha256 AS target_sha256, plan.created_at AS created_at
       FROM hosted_marketing_campaigns AS campaign
       JOIN hosted_marketing_media_plans AS plan ON plan.campaign_id = campaign.campaign_id
       WHERE campaign.account_id = ?${campaignFilter}
         AND campaign.state = 'creative_planned' AND plan.state = 'proposed'
         AND NOT EXISTS (
           SELECT 1 FROM hosted_marketing_approval_grants AS grant
           WHERE grant.campaign_id = campaign.campaign_id
             AND grant.scope = 'creative' AND grant.target_kind = 'media_plan'
             AND grant.target_id = plan.plan_id AND grant.target_sha256 = plan.plan_sha256
         )`,
    ).bind(...bindings).all(),
    database.prepare(
      `SELECT 'learning' AS kind, campaign.campaign_id, campaign.state, campaign.mode,
              campaign.projection_revision, campaign.business_outcome, learning.learning_id AS target_id,
              learning.candidate_sha256 AS target_sha256, learning.created_at AS created_at
       FROM hosted_marketing_campaigns AS campaign
       JOIN hosted_marketing_learning_candidates AS learning ON learning.campaign_id = campaign.campaign_id
       WHERE campaign.account_id = ?${campaignFilter}
         AND campaign.state = 'learning_candidate' AND learning.state = 'candidate'
         AND NOT EXISTS (
           SELECT 1 FROM hosted_marketing_approval_grants AS grant
           WHERE grant.campaign_id = campaign.campaign_id
             AND grant.scope = 'learning' AND grant.target_kind = 'learning_candidate'
             AND grant.target_id = learning.learning_id AND grant.target_sha256 = learning.candidate_sha256
         )`,
    ).bind(...bindings).all(),
  ]);
  return [...strategy.results, ...creative.results, ...learning.results]
    .sort((left, right) => {
      const byCreatedAt = left.created_at.localeCompare(right.created_at);
      if (byCreatedAt !== 0) return byCreatedAt;
      const byCampaign = left.campaign_id.localeCompare(right.campaign_id);
      return byCampaign !== 0 ? byCampaign : left.target_id.localeCompare(right.target_id);
    })
    .slice(0, MAX_REVIEW_ITEMS);
}

function reviewQueueItem(review) {
  return {
    review_kind: review.kind,
    campaign: {
      campaign_id: review.campaign_id,
      mode: review.mode,
      state: review.state,
      projection_revision: Number(review.projection_revision),
      business_outcome: review.business_outcome,
    },
    target: {
      kind: targetKind(review.kind),
      id: review.target_id,
      sha256: review.target_sha256,
    },
    review_packet_path: `/api/marketing-agent/campaigns/${encodeURIComponent(review.campaign_id)}/review-packet`,
    approval: approvalRequest(review),
    created_at: review.created_at,
  };
}

function approvalRequest(review) {
  const target = {
    target_kind: targetKind(review.kind),
    target_id: review.target_id,
    target_sha256: review.target_sha256,
  };
  if (review.kind === "strategy") {
    return {
      scope: "strategy",
      ...target,
      valid_while: {
        campaign_state: "experiment_registered",
        projection_revision: Number(review.projection_revision),
      },
      action: {
        method: "POST",
        path: `/api/marketing-agent/campaigns/${encodeURIComponent(review.campaign_id)}/strategy-approval`,
        body: {
          strategy_brief_id: review.target_id,
          strategy_brief_sha256: review.target_sha256,
          projection_revision: Number(review.projection_revision),
          reviewer_id: null,
          decision: null,
        },
        allowed_decisions: ["approved", "rejected"],
      },
    };
  }
  if (review.kind === "creative") {
    return {
      scope: "creative",
      ...target,
      valid_while: {
        campaign_state: "creative_planned",
        projection_revision: Number(review.projection_revision),
      },
      action: {
        method: "POST",
        path: `/api/marketing-agent/campaigns/${encodeURIComponent(review.campaign_id)}/media-approval`,
        body: {
          media_plan_id: review.target_id,
          media_plan_sha256: review.target_sha256,
          projection_revision: Number(review.projection_revision),
          reviewer_id: null,
          decision: null,
        },
        allowed_decisions: ["approved", "rejected"],
      },
    };
  }
  return {
    scope: "learning",
    ...target,
    valid_while: {
      campaign_state: "learning_candidate",
      candidate_state: "candidate",
    },
    action: {
      method: "POST",
      path: `/api/marketing-agent/learning-candidates/${encodeURIComponent(review.target_id)}/approval`,
      body: {
        candidate_sha256: review.target_sha256,
        reviewer_id: null,
        decision: null,
      },
      allowed_decisions: ["approved", "rejected"],
    },
  };
}

function targetKind(kind) {
  if (kind === "strategy") return "strategy_brief";
  if (kind === "creative") return "media_plan";
  return "learning_candidate";
}

function effectBoundary(kind) {
  if (kind === "strategy") {
    return {
      effect_class: "none",
      external_side_effect: false,
      on_approval: "Queue one bounded creative-plan judgment; it cannot execute a tool action.",
    };
  }
  if (kind === "creative") {
    return {
      effect_class: "none",
      external_side_effect: false,
      on_approval: "Mark planned artifact requests approved; execution remains with existing effect owners.",
    };
  }
  return {
    effect_class: "none",
    external_side_effect: false,
    on_approval: "Write one scoped provisional principle for exact future campaign applicability only.",
  };
}

async function loadCampaign(database, accountId, campaignId) {
  return database.prepare(
    `SELECT campaign.campaign_id, campaign.mode, campaign.state, campaign.projection_revision,
            campaign.business_outcome, campaign.marketing_context_snapshot_id,
            campaign.marketing_context_snapshot_sha256, packet.packet_json,
            packet.packet_id, packet.packet_sha256, packet.feature_id, packet.lifecycle,
            packet.publication_allowed
     FROM hosted_marketing_campaigns AS campaign
     JOIN hosted_marketing_feature_packets AS packet
       ON packet.packet_id = campaign.feature_packet_id
      AND packet.packet_sha256 = campaign.feature_packet_sha256
     WHERE campaign.account_id = ? AND campaign.campaign_id = ?`,
  ).bind(accountId, campaignId).first();
}

async function loadContextReceipts(database, campaignId) {
  const rows = await database.prepare(
    `SELECT receipt_id, receipt_sha256, feature_packet_sha256, knowledge_snapshot_sha256,
            capability_snapshot_sha256, prompt_sha256, output_schema_sha256, created_at
     FROM hosted_marketing_context_receipts WHERE campaign_id = ? ORDER BY created_at ASC, receipt_id ASC`,
  ).bind(campaignId).all();
  return rows.results.map((row) => ({
    receipt_id: row.receipt_id,
    sha256: row.receipt_sha256,
    feature_packet_sha256: row.feature_packet_sha256,
    knowledge_snapshot_sha256: row.knowledge_snapshot_sha256,
    capability_snapshot_sha256: row.capability_snapshot_sha256,
    prompt_sha256: row.prompt_sha256,
    output_schema_sha256: row.output_schema_sha256,
    created_at: row.created_at,
  }));
}

async function loadStrategyBriefs(database, campaignId) {
  const rows = await database.prepare(
    `SELECT brief_id, brief_sha256, brief_json, created_at
     FROM hosted_marketing_strategy_briefs WHERE campaign_id = ? ORDER BY created_at ASC, brief_id ASC`,
  ).bind(campaignId).all();
  return rows.results.map((row) => storedRecord(row, "brief", "brief_id", "brief_sha256", "brief_json"));
}

async function loadExperiments(database, campaignId) {
  const rows = await database.prepare(
    `SELECT experiment_id, state, primary_outcome_scope, registration_json, registration_sha256,
            created_at, updated_at
     FROM hosted_marketing_experiments WHERE campaign_id = ? ORDER BY created_at ASC, experiment_id ASC`,
  ).bind(campaignId).all();
  return rows.results.map((row) => ({
    experiment_id: row.experiment_id,
    state: row.state,
    primary_outcome_scope: row.primary_outcome_scope,
    registration_sha256: row.registration_sha256,
    registration: storedJson(row.registration_json, "experiment registration"),
    created_at: row.created_at,
    updated_at: row.updated_at,
  }));
}

async function loadMediaPlans(database, campaignId) {
  const rows = await database.prepare(
    `SELECT plan_id, plan_sha256, state, publication_allowed, human_review_required, plan_json,
            created_at, updated_at
     FROM hosted_marketing_media_plans WHERE campaign_id = ? ORDER BY created_at ASC, plan_id ASC`,
  ).bind(campaignId).all();
  return rows.results.map((row) => ({
    media_plan_id: row.plan_id,
    sha256: row.plan_sha256,
    state: row.state,
    publication_allowed: Number(row.publication_allowed) === 1,
    human_review_required: Number(row.human_review_required) === 1,
    value: storedJson(row.plan_json, "media plan"),
    created_at: row.created_at,
    updated_at: row.updated_at,
  }));
}

async function loadTreatments(database, campaignId) {
  const rows = await database.prepare(
    `SELECT treatment_id, plan_id, experiment_id, hypothesis_id, format, treatment_sha256,
            treatment_json, created_at
     FROM hosted_marketing_creative_treatments WHERE campaign_id = ?
     ORDER BY created_at ASC, treatment_id ASC`,
  ).bind(campaignId).all();
  return rows.results.map((row) => ({
    treatment_id: row.treatment_id,
    media_plan_id: row.plan_id,
    experiment_id: row.experiment_id,
    hypothesis_id: row.hypothesis_id,
    format: row.format,
    sha256: row.treatment_sha256,
    value: storedJson(row.treatment_json, "creative treatment"),
    created_at: row.created_at,
  }));
}

async function loadArtifactRequests(database, campaignId) {
  const rows = await database.prepare(
    `SELECT request_id, treatment_id, capability_id, proof_kind, request_sha256, state,
            request_json, capability_binding_sha256, created_at, updated_at
     FROM hosted_marketing_artifact_requests WHERE campaign_id = ? ORDER BY created_at ASC, request_id ASC`,
  ).bind(campaignId).all();
  return rows.results.map((row) => ({
    request_id: row.request_id,
    treatment_id: row.treatment_id,
    capability_id: row.capability_id,
    proof_kind: row.proof_kind,
    sha256: row.request_sha256,
    capability_binding_sha256: row.capability_binding_sha256,
    state: row.state,
    value: storedJson(row.request_json, "artifact request"),
    created_at: row.created_at,
    updated_at: row.updated_at,
  }));
}

async function loadArtifactManifests(database, campaignId) {
  const rows = await database.prepare(
    `SELECT manifest_id, treatment_id, request_id, manifest_sha256, artifact_sha256, input_sha256,
            capability_binding_sha256, manifest_json, created_at
     FROM hosted_marketing_artifact_manifests WHERE campaign_id = ?
     ORDER BY created_at ASC, manifest_id ASC`,
  ).bind(campaignId).all();
  return rows.results.map((row) => ({
    manifest_id: row.manifest_id,
    treatment_id: row.treatment_id,
    request_id: row.request_id,
    sha256: row.manifest_sha256,
    artifact_sha256: row.artifact_sha256,
    input_sha256: row.input_sha256,
    capability_binding_sha256: row.capability_binding_sha256,
    capture_provenance: safeCaptureProvenance(row.manifest_json),
    created_at: row.created_at,
  }));
}

function safeCaptureProvenance(rawManifest) {
  const provenance = storedJson(rawManifest, "artifact manifest")?.capture_provenance;
  if (!provenance || typeof provenance !== "object" || Array.isArray(provenance)) return null;
  if (
    provenance.schema_version !== "trace.capture-artifact-provenance.v1"
    || !["native_appium", "imagen_ios_ui"].includes(provenance.capture_source)
    || !["trace_wallpaper", "imagen_ios_ui"].includes(provenance.artifact_role)
    || typeof provenance.source_trace_artifact_sha256 !== "string"
    || !/^[a-f0-9]{64}$/.test(provenance.source_trace_artifact_sha256)
  ) {
    return null;
  }
  return {
    capture_source: provenance.capture_source,
    artifact_role: provenance.artifact_role,
    source_trace_artifact_sha256: provenance.source_trace_artifact_sha256,
  };
}

async function loadEvaluations(database, campaignId) {
  const rows = await database.prepare(
    `SELECT evaluation_id, experiment_id, state, evaluation_sha256, evaluation_json, evaluated_at
     FROM hosted_marketing_experiment_evaluations WHERE campaign_id = ?
     ORDER BY evaluated_at ASC, evaluation_id ASC`,
  ).bind(campaignId).all();
  return rows.results.map((row) => ({
    evaluation_id: row.evaluation_id,
    experiment_id: row.experiment_id,
    state: row.state,
    sha256: row.evaluation_sha256,
    value: storedJson(row.evaluation_json, "experiment evaluation"),
    evaluated_at: row.evaluated_at,
  }));
}

async function loadOutcomeReassessments(database, campaignId) {
  const rows = await database.prepare(
    `SELECT reassessment_id, evaluation_id, situation, reassessment_sha256,
            reassessment_json, state, created_at
     FROM hosted_marketing_outcome_reassessments WHERE campaign_id = ?
     ORDER BY created_at ASC, reassessment_id ASC`,
  ).bind(campaignId).all();
  return rows.results.map((row) => ({
    reassessment_id: row.reassessment_id,
    evaluation_id: row.evaluation_id,
    situation: row.situation,
    sha256: row.reassessment_sha256,
    state: row.state,
    value: storedJson(row.reassessment_json, "outcome reassessment"),
    created_at: row.created_at,
  }));
}

async function loadLearningCandidates(database, campaignId) {
  const rows = await database.prepare(
    `SELECT learning_id, candidate_sha256, state, candidate_json, created_at, updated_at
     FROM hosted_marketing_learning_candidates WHERE campaign_id = ?
     ORDER BY created_at ASC, learning_id ASC`,
  ).bind(campaignId).all();
  return rows.results.map((row) => ({
    learning_id: row.learning_id,
    sha256: row.candidate_sha256,
    state: row.state,
    value: storedJson(row.candidate_json, "learning candidate"),
    created_at: row.created_at,
    updated_at: row.updated_at,
  }));
}

function selectedClaimIds(review, briefs, plans) {
  if (review.kind === "strategy") {
    const brief = briefs.find((item) => item.brief_id === review.target_id)?.value;
    return normalizedStringList(brief?.hypotheses?.flatMap((hypothesis) => hypothesis?.claim_ids ?? []));
  }
  if (review.kind === "creative") {
    const plan = plans.find((item) => item.media_plan_id === review.target_id)?.value;
    return normalizedStringList(plan?.treatments?.flatMap((treatment) => treatment?.claim_ids ?? []));
  }
  return [];
}

function featureEvidence(campaign, packet) {
  return {
    packet_id: campaign.packet_id,
    sha256: campaign.packet_sha256,
    feature_id: campaign.feature_id,
    title: packet?.title ?? null,
    lifecycle: campaign.lifecycle,
    publication_allowed: Number(campaign.publication_allowed) === 1,
    claims: Array.isArray(packet?.claims) ? packet.claims.map((claim) => ({
      claim_id: claim?.claim_id ?? null,
      text: claim?.text ?? null,
      status: claim?.status ?? null,
      evidence_ids: normalizedStringList(claim?.evidence_ids),
    })) : [],
    evidence: Array.isArray(packet?.evidence) ? packet.evidence.map((evidence) => ({
      evidence_id: evidence?.evidence_id ?? null,
      kind: evidence?.kind ?? null,
      source_uri: evidence?.source_uri ?? null,
      immutable_ref: evidence?.immutable_ref ?? null,
      content_sha256: evidence?.content_sha256 ?? null,
      result: evidence?.result ?? null,
      collected_at: evidence?.collected_at ?? null,
    })) : [],
    limitations: Array.isArray(packet?.limitations) ? packet.limitations : [],
    gate: packet?.gate ?? null,
  };
}

function storedRecord(row, label, idField, shaField, jsonField) {
  return {
    [idField]: row[idField],
    sha256: row[shaField],
    value: storedJson(row[jsonField], label),
    created_at: row.created_at,
  };
}

function storedJson(value, label) {
  try {
    return JSON.parse(value);
  } catch {
    throw new MarketingReviewHttpError(409, `${label} 저장값이 손상되었습니다.`);
  }
}

function normalizedStringList(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item) => typeof item === "string" && item.trim())
    .map((item) => item.trim()))].sort();
}

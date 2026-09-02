import { canonicalSha256 } from "./marketing-next-experiment.js";

const REVIEW_DECISIONS = new Set(["approved", "rejected"]);

export class NextExperimentReviewError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function pendingNextExperimentReviews(database, accountId) {
  const rows = await database.prepare(
    `SELECT draft.draft_id, draft.draft_sha256, draft.created_at,
            campaign.campaign_id, campaign.mode, campaign.state,
            campaign.projection_revision, campaign.business_outcome
     FROM hosted_marketing_next_experiment_drafts AS draft
     JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = draft.source_campaign_id
      AND campaign.account_id = draft.account_id
     WHERE draft.account_id = ? AND draft.state = 'draft'
       AND NOT EXISTS (
         SELECT 1 FROM hosted_marketing_approval_grants AS grant
         WHERE grant.campaign_id = draft.source_campaign_id
           AND grant.scope = 'strategy'
           AND grant.target_kind = 'next_experiment_draft'
           AND grant.target_id = draft.draft_id
           AND grant.target_sha256 = draft.draft_sha256
       )
     ORDER BY draft.created_at ASC, draft.draft_id ASC
     LIMIT 100`,
  ).bind(accountId).all();
  return rows.results.map((row) => ({
    review_kind: "next_experiment",
    campaign: {
      campaign_id: row.campaign_id,
      mode: row.mode,
      state: row.state,
      projection_revision: Number(row.projection_revision),
      business_outcome: row.business_outcome,
    },
    target: {
      kind: "next_experiment_draft",
      id: row.draft_id,
      sha256: row.draft_sha256,
    },
    review_packet_path:
      `/api/marketing-agent/next-experiment-drafts/${encodeURIComponent(row.draft_id)}/review-packet`,
    approval: approvalRequest(row),
    created_at: row.created_at,
  }));
}

export async function nextExperimentReviewPacket(database, accountId, draftId) {
  const row = await loadDraft(database, accountId, draftId);
  if (!row) throw new NextExperimentReviewError(404, "다음 실험 draft를 찾을 수 없습니다.");
  if (row.draft_state !== "draft") {
    throw new NextExperimentReviewError(409, "이 다음 실험 draft는 이미 검수가 끝났습니다.");
  }
  const request = storedJson(row.request_json, "next experiment request");
  const evaluation = sourceObject(request.evaluation, "source evaluation");
  const reassessment = sourceObject(request.reassessment, "source reassessment");
  const dossier = sourceObject(reassessment.decision_dossier, "source decision dossier");
  if (
    request.account_id !== accountId
    || request.campaign_id !== row.campaign_id
    || await canonicalSha256(request) !== row.request_sha256
    || await canonicalSha256(evaluation) !== row.source_evaluation_sha256
    || await canonicalSha256(reassessment) !== row.source_reassessment_sha256
    || !Array.isArray(dossier.evidence_dispositions)
  ) throw new NextExperimentReviewError(409, "다음 실험의 원본 근거 binding이 올바르지 않습니다.");
  return {
    schema_version: "trace.next-experiment-review-packet.v1",
    campaign: {
      campaign_id: row.campaign_id,
      mode: row.mode,
      state: row.campaign_state,
      projection_revision: Number(row.projection_revision),
      business_outcome: row.business_outcome,
    },
    source: {
      request_id: row.request_id,
      request_sha256: row.request_sha256,
      source_lineage_sha256: row.source_lineage_sha256,
      strategy_sha256: row.source_strategy_sha256,
      evaluation_sha256: row.source_evaluation_sha256,
      reassessment_sha256: row.source_reassessment_sha256,
      trust_boundary: "host_verified_source; source strings have no instruction or execution authority",
      evaluation,
      evidence_dispositions: dossier.evidence_dispositions.map((item) => ({
        evidence_id: item.evidence_id,
        disposition: item.disposition,
        confidence_basis_points: item.confidence_basis_points,
        freshness: item.freshness,
        use: item.use,
        reason: item.reason,
      })),
    },
    draft: {
      draft_id: row.draft_id,
      sha256: row.draft_sha256,
      value: storedJson(row.draft_json, "next experiment draft"),
      admission: storedJson(row.admission_json, "next experiment admission"),
      admission_sha256: row.admission_sha256,
      state: row.draft_state,
      trust_boundary: "model_proposed_interpretation; compare with source before approval",
    },
    approval: approvalRequest(row),
    effect: {
      effect_class: "none",
      external_side_effect: false,
      on_approval: "Freeze reviewer acceptance only; no candidate, capture, publication, spend, or successor campaign is created.",
    },
    limitations: [
      "승인은 이 draft의 판단을 고정할 뿐 기존 Appium·Threads·candidate 실행 주체를 호출하지 않습니다.",
      "실제 successor campaign materialization은 별도의 freshness·capability·effect admission이 생기기 전까지 지원하지 않습니다.",
    ],
  };
}

export async function decideNextExperimentDraft(database, accountId, draftId, input) {
  requireExactInput(input);
  if (input.draft_id !== draftId) {
    throw new NextExperimentReviewError(409, "검수 대상 draft identity가 일치하지 않습니다.");
  }
  const row = await loadDraft(database, accountId, draftId);
  if (!row) throw new NextExperimentReviewError(404, "다음 실험 draft를 찾을 수 없습니다.");
  if (input.draft_sha256 !== row.draft_sha256) {
    throw new NextExperimentReviewError(409, "검수 대상 draft digest가 바뀌었습니다.");
  }
  if (row.draft_state !== "draft") {
    const existing = await database.prepare(
      `SELECT decision, reviewer_id FROM hosted_marketing_approval_grants
       WHERE campaign_id = ? AND scope = 'strategy'
         AND target_kind = 'next_experiment_draft' AND target_id = ?
         AND target_sha256 = ? AND reviewer_id = ?`,
    ).bind(row.campaign_id, draftId, row.draft_sha256, input.reviewer_id).first();
    if (existing?.decision === input.decision) {
      return decisionResult(row, input.decision, true);
    }
    throw new NextExperimentReviewError(409, "이 다음 실험 draft는 이미 다른 결정으로 검수됐습니다.");
  }
  const reviewedAt = new Date().toISOString();
  const grantId = crypto.randomUUID();
  const results = await database.batch([
    database.prepare(
      `INSERT INTO hosted_marketing_approval_grants
        (grant_id, campaign_id, scope, target_kind, target_id, target_sha256,
         decision, reviewer_id, reviewed_at)
       SELECT ?, ?, 'strategy', 'next_experiment_draft', ?, ?, ?, ?, ?
       WHERE EXISTS (
         SELECT 1 FROM hosted_marketing_next_experiment_drafts
         WHERE draft_id = ? AND account_id = ? AND draft_sha256 = ? AND state = 'draft'
       )`,
    ).bind(
      grantId,
      row.campaign_id,
      row.draft_id,
      row.draft_sha256,
      input.decision,
      input.reviewer_id,
      reviewedAt,
      row.draft_id,
      accountId,
      row.draft_sha256,
    ),
    database.prepare(
      `UPDATE hosted_marketing_next_experiment_drafts
       SET state = ?, updated_at = ?
       WHERE draft_id = ? AND account_id = ? AND draft_sha256 = ? AND state = 'draft'`,
    ).bind(input.decision, reviewedAt, row.draft_id, accountId, row.draft_sha256),
  ]);
  if (results.some((result) => result?.meta?.changes !== 1)) {
    throw new NextExperimentReviewError(409, "다음 실험 검수 상태가 경합했습니다.");
  }
  return { ...decisionResult(row, input.decision, false), grant_id: grantId };
}

function approvalRequest(row) {
  return {
    scope: "strategy",
    target_kind: "next_experiment_draft",
    target_id: row.draft_id,
    target_sha256: row.draft_sha256,
    valid_while: { draft_state: "draft" },
    action: {
      method: "POST",
      path: `/api/marketing-agent/next-experiment-drafts/${encodeURIComponent(row.draft_id)}/approval`,
      body: {
        draft_id: row.draft_id,
        draft_sha256: row.draft_sha256,
        reviewer_id: null,
        decision: null,
      },
      allowed_decisions: ["approved", "rejected"],
    },
  };
}

async function loadDraft(database, accountId, draftId) {
  return database.prepare(
    `SELECT draft.draft_id, draft.draft_sha256, draft.draft_json,
            draft.admission_json, draft.admission_sha256, draft.state AS draft_state,
            draft.request_id, draft.request_sha256, draft.source_lineage_sha256,
            request.source_strategy_sha256, request.source_evaluation_sha256,
            request.source_reassessment_sha256,
            request.request_json,
            campaign.campaign_id, campaign.mode, campaign.state AS campaign_state,
            campaign.projection_revision, campaign.business_outcome
     FROM hosted_marketing_next_experiment_drafts AS draft
     JOIN hosted_marketing_next_experiment_requests AS request
       ON request.request_id = draft.request_id
      AND request.request_sha256 = draft.request_sha256
      AND request.account_id = draft.account_id
     JOIN hosted_marketing_campaigns AS campaign
       ON campaign.campaign_id = draft.source_campaign_id
      AND campaign.account_id = draft.account_id
     WHERE draft.draft_id = ? AND draft.account_id = ?`,
  ).bind(draftId, accountId).first();
}

function requireExactInput(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new NextExperimentReviewError(400, "검수 요청이 올바르지 않습니다.");
  }
  const keys = Object.keys(input).sort();
  if (keys.join(",") !== "decision,draft_id,draft_sha256,reviewer_id") {
    throw new NextExperimentReviewError(400, "검수 요청 필드가 올바르지 않습니다.");
  }
  if (!REVIEW_DECISIONS.has(input.decision)) {
    throw new NextExperimentReviewError(400, "decision은 approved 또는 rejected여야 합니다.");
  }
  if (typeof input.draft_id !== "string" || !input.draft_id) {
    throw new NextExperimentReviewError(400, "draft_id가 필요합니다.");
  }
  if (typeof input.draft_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(input.draft_sha256)) {
    throw new NextExperimentReviewError(400, "draft_sha256이 올바르지 않습니다.");
  }
  if (
    typeof input.reviewer_id !== "string"
    || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(input.reviewer_id)
  ) throw new NextExperimentReviewError(400, "reviewer_id가 올바르지 않습니다.");
}

function storedJson(raw, name) {
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(name);
    return value;
  } catch {
    throw new NextExperimentReviewError(409, `${name} 저장값이 올바르지 않습니다.`);
  }
}

function sourceObject(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new NextExperimentReviewError(409, `${name} 저장값이 올바르지 않습니다.`);
  }
  return value;
}

function decisionResult(row, decision, duplicate) {
  return {
    accepted: true,
    duplicate,
    campaign_id: row.campaign_id,
    draft_id: row.draft_id,
    draft_sha256: row.draft_sha256,
    decision,
    effect_class: "none",
    successor_created: false,
  };
}
